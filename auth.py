from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_user, logout_user, login_required, current_user
from models import User, init_db

auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    if request.method == 'POST':
        email = request.form['email']
        senha = request.form['senha']
        user = User.find_by_email(email)
        if user and user.verificar_senha(senha):
            login_user(user)
            return redirect(url_for('index'))
        else:
            flash('E-mail ou senha inválidos', 'danger')
    return render_template('login.html')

@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    if request.method == 'POST':
        nome = request.form['nome']
        email = request.form['email']
        senha = request.form['senha']
        confirm = request.form['confirm']
        if senha != confirm:
            flash('As senhas não coincidem', 'warning')
        elif User.find_by_email(email):
            flash('E-mail já cadastrado', 'danger')
        else:
            user = User.create(email, nome, senha)
            login_user(user)
            return redirect(url_for('index'))
    return render_template('register.html')

@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('auth.login'))