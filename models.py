import sqlite3
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

DATABASE = 'domino.db'

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Cria as tabelas necessárias."""
    with get_db() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                nome TEXT NOT NULL,
                senha_hash TEXT NOT NULL,
                moedas INTEGER DEFAULT 0,
                premium BOOLEAN DEFAULT 0,
                data_criacao TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS placar (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                vitorias_jogador INTEGER DEFAULT 0,
                vitorias_ia INTEGER DEFAULT 0,
                empates INTEGER DEFAULT 0,
                total_partidas INTEGER DEFAULT 0,
                receita_total REAL DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                tipo TEXT NOT NULL,  -- ex: 'maior_sequencia_vitorias', 'maior_pontuacao'
                valor INTEGER NOT NULL,
                data_registro TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id),
                UNIQUE(user_id, tipo)
            )
        ''')
        conn.commit()

class User(UserMixin):
    def __init__(self, id, email, nome, moedas, premium):
        self.id = id
        self.email = email
        self.nome = nome
        self.moedas = moedas
        self.premium = bool(premium)

    @staticmethod
    def get(user_id):
        with get_db() as conn:
            user_row = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
            if user_row:
                return User(user_row['id'], user_row['email'], user_row['nome'], user_row['moedas'], user_row['premium'])
            return None

    @staticmethod
    def find_by_email(email):
        with get_db() as conn:
            user_row = conn.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()
            if user_row:
                return User(user_row['id'], user_row['email'], user_row['nome'], user_row['moedas'], user_row['premium'])
            return None

    @staticmethod
    def create(email, nome, senha):
        senha_hash = generate_password_hash(senha)
        with get_db() as conn:
            cursor = conn.execute(
                'INSERT INTO users (email, nome, senha_hash) VALUES (?, ?, ?)',
                (email, nome, senha_hash)
            )
            user_id = cursor.lastrowid
            # Inicializa placar para o usuário
            conn.execute('INSERT INTO placar (user_id) VALUES (?)', (user_id,))
            conn.commit()
            return User.get(user_id)

    def verificar_senha(self, senha):
        with get_db() as conn:
            row = conn.execute('SELECT senha_hash FROM users WHERE id = ?', (self.id,)).fetchone()
            if row:
                return check_password_hash(row['senha_hash'], senha)
            return False

    def atualizar_moedas(self, delta):
        with get_db() as conn:
            conn.execute('UPDATE users SET moedas = moedas + ? WHERE id = ?', (delta, self.id))
            conn.commit()
            self.moedas += delta

    def tornar_premium(self):
        with get_db() as conn:
            conn.execute('UPDATE users SET premium = 1 WHERE id = ?', (self.id,))
            conn.commit()
            self.premium = True

    def obter_placar(self):
        with get_db() as conn:
            row = conn.execute('SELECT * FROM placar WHERE user_id = ?', (self.id,)).fetchone()
            if row:
                return dict(row)
            return {'vitorias_jogador':0, 'vitorias_ia':0, 'empates':0, 'total_partidas':0, 'receita_total':0.0}

    def atualizar_placar(self, vencedor):
        """vencedor: 'jogador', 'ia', 'empate'"""
        with get_db() as conn:
            placar = self.obter_placar()
            novos = {}
            if vencedor == 'jogador':
                novos['vitorias_jogador'] = placar['vitorias_jogador'] + 1
            elif vencedor == 'ia':
                novos['vitorias_ia'] = placar['vitorias_ia'] + 1
            else:
                novos['empates'] = placar['empates'] + 1
            novos['total_partidas'] = placar['total_partidas'] + 1
            if not self.premium:
                novos['receita_total'] = placar['receita_total'] + 0.01
            conn.execute('''
                UPDATE placar SET
                    vitorias_jogador = ?,
                    vitorias_ia = ?,
                    empates = ?,
                    total_partidas = ?,
                    receita_total = ?
                WHERE user_id = ?
            ''', (novos.get('vitorias_jogador', placar['vitorias_jogador']),
                  novos.get('vitorias_ia', placar['vitorias_ia']),
                  novos.get('empates', placar['empates']),
                  novos['total_partidas'],
                  novos.get('receita_total', placar['receita_total']),
                  self.id))
            conn.commit()

    def obter_record(self, tipo):
        with get_db() as conn:
            row = conn.execute('SELECT valor FROM records WHERE user_id = ? AND tipo = ?', (self.id, tipo)).fetchone()
            return row['valor'] if row else 0

    def atualizar_record(self, tipo, valor):
        with get_db() as conn:
            atual = self.obter_record(tipo)
            if valor > atual:
                conn.execute('''
                    INSERT OR REPLACE INTO records (user_id, tipo, valor, data_registro)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ''', (self.id, tipo, valor))
                conn.commit()
                return True
        return False