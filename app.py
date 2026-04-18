import hashlib
import json
import logging
import os
from typing import Dict, List, Optional, Tuple
from flask import Flask, request, jsonify, render_template, redirect, url_for, flash
from flask_cors import CORS
from flask_login import LoginManager, login_required, current_user, login_user, logout_user
from dotenv import load_dotenv

# Carrega variáveis do arquivo .env
load_dotenv()

# Configura logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-key-mude-em-producao')

# Configura CORS apenas para rotas de API (opcional, já que frontend na mesma origem)
CORS(app, resources={r"/api/*": {"origins": "*"}})

# ============================================================
# Importação dos modelos e blueprint de autenticação
# ============================================================
from models import init_db, User
from auth import auth_bp

# Inicializa banco de dados
init_db()

# Configura Flask-Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'auth.login'

@login_manager.user_loader
def load_user(user_id):
    return User.get(int(user_id))

# Registra blueprint de autenticação
app.register_blueprint(auth_bp)

# ============================================================
# Configuração da LLM via .env (opcional)
# ============================================================
USE_LLM = os.getenv("USE_LLM", "false").lower() == "true"
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

if USE_LLM and GROQ_API_KEY:
    try:
        from groq import Groq
        client = Groq(api_key=GROQ_API_KEY)
        logger.info("Groq client inicializado com sucesso.")
    except ImportError:
        logger.warning("Biblioteca 'groq' não instalada. Instale com: pip install groq")
        USE_LLM = False
    except Exception as e:
        logger.error(f"Erro ao inicializar Groq: {e}")
        USE_LLM = False
else:
    if USE_LLM and not GROQ_API_KEY:
        logger.warning("USE_LLM=True mas GROQ_API_KEY não definida no .env. Desativando LLM.")
        USE_LLM = False
    else:
        logger.info("LLM desativada (USE_LLM=false). Usando apenas minimax.")

# Cache simples para jogadas
cache: Dict[str, Dict] = {}

# ============================================================
# 1. HEURÍSTICA E LÓGICA DO DOMINÓ (IA)
# ============================================================
def avaliar_estado(estado: Dict) -> int:
    """Avalia o estado atual da IA (quanto maior, melhor)."""
    mao = estado.get("mao", [])
    extremidades = estado.get("extremidades", [None, None])
    score = 0

    soma_total = sum(p[0] + p[1] for p in mao)
    score += soma_total

    for p in mao:
        if p[0] == p[1]:
            score += 15

    freq = {}
    for p in mao:
        freq[p[0]] = freq.get(p[0], 0) + 1
        freq[p[1]] = freq.get(p[1], 0) + 1
    for count in freq.values():
        if count > 3:
            score -= 10 * (count - 3)

    for p in mao:
        if extremidades[0] is not None and (p[0] == extremidades[0] or p[1] == extremidades[0]):
            score += 5
        if extremidades[1] is not None and (p[0] == extremidades[1] or p[1] == extremidades[1]):
            score += 5
    return score

def simular_jogada(estado: Dict, jogada: Dict) -> Dict:
    novo_estado = {
        "mao": estado["mao"][:],
        "extremidades": estado["extremidades"][:],
        "mesa": estado.get("mesa", [])[:]
    }
    peca = jogada["peca"][:]
    lado = jogada["lado"]
    indice = jogada["indice"]
    novo_estado["mao"].pop(indice)
    if lado == "esquerda":
        if peca[1] == novo_estado["extremidades"][0]:
            novo_estado["extremidades"][0] = peca[0]
        elif peca[0] == novo_estado["extremidades"][0]:
            novo_estado["extremidades"][0] = peca[1]
    else:
        if peca[0] == novo_estado["extremidades"][1]:
            novo_estado["extremidades"][1] = peca[1]
        elif peca[1] == novo_estado["extremidades"][1]:
            novo_estado["extremidades"][1] = peca[0]
    return novo_estado

def gerar_jogadas(estado: Dict) -> List[Dict]:
    extremidades = estado["extremidades"]
    mao = estado["mao"]
    jogadas = []
    for idx, peca in enumerate(mao):
        if extremidades[0] is None or peca[0] == extremidades[0] or peca[1] == extremidades[0]:
            jogadas.append({"peca": peca, "lado": "esquerda", "indice": idx})
        if extremidades[1] is not None and (peca[0] == extremidades[1] or peca[1] == extremidades[1]):
            if not (extremidades[0] == extremidades[1] and jogadas and jogadas[-1]["peca"] == peca):
                jogadas.append({"peca": peca, "lado": "direita", "indice": idx})
    return jogadas

def minimax(estado: Dict, profundidade: int = 2) -> List[Tuple[Optional[Dict], int]]:
    jogadas = gerar_jogadas(estado)
    if not jogadas or profundidade == 0:
        return [(None, avaliar_estado(estado))]
    resultados = []
    for jogada in jogadas:
        novo_estado = simular_jogada(estado, jogada)
        filhos = minimax(novo_estado, profundidade - 1)
        pior_score = min(score for _, score in filhos) if filhos else avaliar_estado(novo_estado)
        resultados.append((jogada, pior_score))
    return resultados

def consultar_llm(prompt: str) -> Optional[str]:
    if not USE_LLM or not GROQ_API_KEY:
        return None
    try:
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",  # <-- modelo atualizado
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=100
        )
        return completion.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Erro na LLM: {e}")
        return None
        
def escolher_com_llm(melhores_jogadas: List[Dict], estado: Dict) -> Optional[Dict]:
    if not USE_LLM:
        return None
    prompt = f"""
Você é um especialista em dominó. Dada a situação atual:

Mesa: {estado.get('mesa', [])}
Extremidades: {estado.get('extremidades', [None, None])}
Suas peças: {estado.get('mao', [])}

As melhores jogadas possíveis (segundo análise heurística) são:
{json.dumps(melhores_jogadas, indent=2)}

Escolha a melhor jogada considerando:
- Prefira jogar peças altas primeiro.
- Use dobras estrategicamente para controlar o jogo.
- Evite deixar extremidades que favoreçam o oponente.

Responda APENAS com a jogada no formato JSON exato, por exemplo:
{{"peca": [3,4], "lado": "direita", "indice": 2}}
Não inclua texto adicional.
"""
    resposta = consultar_llm(prompt)
    if resposta:
        try:
            import re
            match = re.search(r'\{.*\}', resposta, re.DOTALL)
            if match:
                jogada = json.loads(match.group())
                if "peca" in jogada and "lado" in jogada and "indice" in jogada:
                    return jogada
        except json.JSONDecodeError:
            logger.warning(f"LLM retornou JSON inválido: {resposta}")
    return None

# ============================================================
# 2. ROTAS DA API (PARA O JOGO)
# ============================================================
@app.route("/api/user_data", methods=["GET"])
@login_required
def api_user_data():
    """Retorna os dados do usuário logado (moedas, premium, placar)."""
    placar = current_user.obter_placar()
    return jsonify({
        "id": current_user.id,
        "nome": current_user.nome,
        "moedas": current_user.moedas,
        "premium": current_user.premium,
        "placar": placar
    })

@app.route("/api/adicionar_moedas", methods=["POST"])
@login_required
def api_adicionar_moedas():
    data = request.json
    qtd = data.get("qtd", 0)
    if qtd > 0:
        current_user.atualizar_moedas(qtd)
        return jsonify({"moedas": current_user.moedas, "success": True})
    return jsonify({"error": "Quantidade inválida"}), 400

@app.route("/api/gastar_moedas", methods=["POST"])
@login_required
def api_gastar_moedas():
    data = request.json
    qtd = data.get("qtd", 0)
    if qtd > 0 and current_user.moedas >= qtd:
        current_user.atualizar_moedas(-qtd)
        return jsonify({"moedas": current_user.moedas, "success": True})
    return jsonify({"error": "Saldo insuficiente"}), 400

@app.route("/api/tornar_premium", methods=["POST"])
@login_required
def api_tornar_premium():
    current_user.tornar_premium()
    return jsonify({"premium": True})

@app.route("/api/registrar_partida", methods=["POST"])
@login_required
def api_registrar_partida():
    data = request.json
    vencedor = data.get("vencedor")  # 'jogador', 'ia', 'empate'
    if vencedor in ("jogador", "ia", "empate"):
        current_user.atualizar_placar(vencedor)
        # Atualiza recorde de sequência de vitórias (opcional, via session)
        from flask import session
        sequencia = session.get("sequencia_vitorias", 0)
        if vencedor == "jogador":
            sequencia += 1
            current_user.atualizar_record("maior_sequencia_vitorias", sequencia)
        else:
            sequencia = 0
        session["sequencia_vitorias"] = sequencia
        return jsonify({"success": True})
    return jsonify({"error": "Vencedor inválido"}), 400

@app.route("/api/jogada", methods=["POST"])
@login_required
def api_jogada():
    """Endpoint para a IA decidir a jogada (protegido)."""
    try:
        data = request.json
        logger.info(f"Recebido payload: {data}")

        estado = {
            "mesa": data.get("mesa", []),
            "extremidades": data.get("extremidades", [None, None]),
            "mao": data.get("maoIA", []),
            "jogadas_possiveis": data.get("jogadasPossiveis", [])
        }

        if not estado["mao"]:
            return jsonify({"jogada": None})

        jogadas = gerar_jogadas(estado)
        if not jogadas:
            return jsonify({"jogada": None})

        cache_key = hashlib.md5(json.dumps(estado, sort_keys=True).encode()).hexdigest()
        if cache_key in cache:
            logger.info("Usando cache")
            return jsonify({"jogada": cache[cache_key]})

        resultados = minimax(estado, profundidade=2)
        resultados = [(j, s) for j, s in resultados if j is not None]
        if not resultados:
            return jsonify({"jogada": None})

        resultados.sort(key=lambda x: x[1], reverse=True)
        melhores_jogadas = [j for j, _ in resultados[:3]]

        jogada_escolhida = None
        if USE_LLM:
            jogada_escolhida = escolher_com_llm(melhores_jogadas, estado)

        if not jogada_escolhida:
            jogada_escolhida = melhores_jogadas[0]

        if jogada_escolhida["indice"] >= len(estado["mao"]):
            jogada_escolhida = melhores_jogadas[0]

        resposta = {
            "peca": jogada_escolhida["peca"],
            "lado": jogada_escolhida["lado"],
            "indice": jogada_escolhida["indice"]
        }

        cache[cache_key] = resposta
        if len(cache) > 100:
            oldest = next(iter(cache))
            del cache[oldest]

        logger.info(f"Jogada escolhida: {resposta}")
        return jsonify({"jogada": resposta})

    except Exception as e:
        logger.exception("Erro no endpoint /api/jogada")
        return jsonify({"jogada": None, "error": str(e)}), 500

@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "use_llm": USE_LLM})

# ============================================================
# 3. ROTA PRINCIPAL (PROTEGIDA)
# ============================================================
@app.route("/")
@login_required
def index():
    """Renderiza a página principal do jogo com dados do usuário."""
    placar = current_user.obter_placar()
    return render_template("index.html",
                           user=current_user,
                           placar=placar)

# ============================================================
# 4. INICIALIZAÇÃO
# ============================================================
if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)