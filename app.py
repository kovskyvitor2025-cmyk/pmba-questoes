from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from markupsafe import Markup, escape
import re
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from datetime import datetime, date, time
import csv
import io
import os

app = Flask(__name__)

# Production configuration:
# - SECRET_KEY must be supplied by the hosting environment.
# - DATABASE_URL is PostgreSQL online and SQLite locally.
# Render/Supabase may provide postgres:// or postgresql:// URLs; SQLAlchemy
# expects the explicit postgresql+psycopg2 scheme for the installed driver.
secret_key = os.environ.get('SECRET_KEY')
if not secret_key:
    secret_key = 'dev-only-change-this-key'
    if os.environ.get('FLASK_ENV') == 'production':
        raise RuntimeError('SECRET_KEY não configurada no ambiente de produção.')

database_url = os.environ.get('DATABASE_URL', 'sqlite:///questoes.db')
if database_url.startswith('postgres://'):
    database_url = database_url.replace('postgres://', 'postgresql+psycopg2://', 1)
elif database_url.startswith('postgresql://'):
    database_url = database_url.replace('postgresql://', 'postgresql+psycopg2://', 1)

app.config['SECRET_KEY'] = secret_key
app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
WHATSAPP_NUMBER = os.environ.get('WHATSAPP_NUMBER', '').replace('+', '').replace(' ', '').replace('-', '').replace('(', '').replace(')', '')
db = SQLAlchemy(app)


@app.template_filter('format_text_base')
def format_text_base(text):
    """Format imported text-base content for reading.

    PDF/CSV imports may contain <br> tags that represent visual line wrapping
    rather than real paragraph breaks. A single <br> is therefore normalized
    to a space; two or more consecutive <br> tags become a paragraph break.
    No arbitrary HTML is rendered.
    """
    if not text:
        return Markup('')
    raw = str(text).replace('\\r\\n', '\\n').replace('\\r', '\\n')
    # Preserve real paragraph breaks first.
    raw = re.sub(r'(?i)(?:\\s*<br\\s*/?>\\s*){2,}', '\\n\\n', raw)
    # A single <br> from PDF line wrapping is just a word-space.
    raw = re.sub(r'(?i)\\s*<br\\s*/?>\\s*', ' ', raw)
    # Normalize whitespace without destroying paragraph separation.
    paragraphs = []
    for para in re.split(r'\\n\\s*\\n+', raw):
        para = re.sub(r'[ \\t\\n]+', ' ', para).strip()
        if para:
            paragraphs.append(f'<p>{escape(para)}</p>')
    return Markup(''.join(paragraphs))


@app.template_filter('format_explanation')
def format_explanation(text):
    """Render question explanations with readable sections and alternatives.

    Supports plain text, line breaks and literal <br> tags without allowing
    arbitrary HTML to be rendered.
    """
    if not text:
        return Markup('')
    raw = re.sub(r'<br\s*/?>', '\n', str(text), flags=re.IGNORECASE)
    raw = raw.replace('\r\n', '\n').replace('\r', '\n')
    raw = re.sub(r'[ \t]+', ' ', raw)
    raw = re.sub(r'\n{3,}', '\n\n', raw).strip()

    def esc(v):
        return str(escape(v)).replace('\n', '<br>')

    parts = []
    # Split the common two-section format used by imported explanations.
    m = re.search(r'(?i)(por que as outras est(?:ã|a)o erradas\s*\?)', raw)
    if m:
        before = raw[:m.start()].strip()
        after = raw[m.end():].strip()
        # First question sentence as heading, if present.
        hm = re.match(r'(?is)^(.*?\?)\s*(.*)$', before)
        if hm:
            parts.append(f'<div class="explanation-section"><h3>{esc(hm.group(1))}</h3><p>{esc(hm.group(2).strip())}</p></div>')
        else:
            parts.append(f'<div class="explanation-section"><p>{esc(before)}</p></div>')
        parts.append(f'<div class="explanation-section"><h3>Por que as outras estão erradas?</h3>{format_alternatives_html(after, esc)}</div>')
        return Markup(''.join(parts))

    # If there is no explicit second heading, still separate A-E explanations.
    return Markup(format_alternatives_html(raw, esc))


def format_alternatives_html(raw, esc):
    chunks = re.split(r'(?=\([A-E]\)\s*(?:Incorreta|Correta)\b)', raw, flags=re.IGNORECASE)
    html_parts = []
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue
        m = re.match(r'(?is)^\(([A-E])\)\s*(Correta|Incorreta)\s*:?\s*(.*)$', chunk)
        if m:
            letter, status, body = m.groups()
            cls = 'correct-explanation' if status.lower() == 'correta' else 'wrong-explanation'
            html_parts.append(f'<div class="explanation-alternative {cls}"><div class="explanation-alt-title"><span class="explanation-letter">{letter}</span><strong>{status}</strong></div><p>{esc(body)}</p></div>')
        else:
            for para in re.split(r'\n\s*\n|\n', chunk):
                para = para.strip()
                if para:
                    html_parts.append(f'<p>{esc(para)}</p>')
    return ''.join(html_parts)

LEVELS = [
    ('Aluno-SD', 0), ('SD', 100), ('Cabo', 300), ('3º Sargento', 600),
    ('2º Sargento', 1000), ('1º Sargento', 1500), ('Subtenente', 2200),
    ('3º Tenente', 3000), ('2º Tenente', 4000), ('1º Tenente', 5500), ('Capitão', 7500)
]
CATEGORIES = [('SD', 'Soldado (SD)'), ('CFO', 'Oficial (CFO)')]
CATEGORY_VALUES = {'SD', 'CFO'}
BASE_XP_ACERTO = 3
BONUS_5_ACERTOS = 20
BONUS_10_ACERTOS = 50
FREE_DAILY_LIMIT = 20
PLANS = {
    'FREE': 'Gratuito',
    'ELITE_SD': 'Elite SD',
    'ELITE_CFO': 'Elite CFO',
    'ELITE_PRO': 'Elite Pro SD + CFO',
}
PLAN_PRICES = {
    'FREE': 0.00,
    'ELITE_SD': 12.90,
    'ELITE_CFO': 12.90,
    'ELITE_PRO': 19.90,
}
PLAN_ACCESS = {
    'FREE': {'SD', 'CFO'},
    'ELITE_SD': {'SD'},
    'ELITE_CFO': {'CFO'},
    'ELITE_PRO': {'SD', 'CFO'},
}

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(160), unique=True, nullable=False)
    telefone = db.Column(db.String(30), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    xp = db.Column(db.Integer, default=0, nullable=False)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    plano = db.Column(db.String(20), default='FREE', nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    answers = db.relationship('Answer', backref='user', lazy=True, cascade='all, delete-orphan')
    comments = db.relationship('Comment', backref='user', lazy=True, cascade='all, delete-orphan')
    feedbacks = db.relationship('Feedback', backref='user', lazy=True, cascade='all, delete-orphan')


class Materia(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    categoria = db.Column(db.String(10), nullable=False)
    nome = db.Column(db.String(160), nullable=False)
    ordem = db.Column(db.Integer, default=0, nullable=False)
    conteudos = db.relationship('Conteudo', backref='materia', lazy=True, cascade='all, delete-orphan')
    __table_args__ = (db.UniqueConstraint('categoria', 'nome', name='uq_materia_categoria_nome'),)

class Conteudo(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    materia_id = db.Column(db.Integer, db.ForeignKey('materia.id'), nullable=False)
    nome = db.Column(db.String(500), nullable=False)
    ordem = db.Column(db.Integer, default=0, nullable=False)
    __table_args__ = (db.UniqueConstraint('materia_id', 'nome', name='uq_conteudo_materia_nome'),)

class Question(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    categoria = db.Column(db.String(10), nullable=False, default='SD')
    banca = db.Column(db.String(120), nullable=False)
    ano = db.Column(db.Integer, nullable=False)
    disciplina = db.Column(db.String(120), nullable=False, default='Português')
    assunto = db.Column(db.String(500), nullable=True)
    materia_id = db.Column(db.Integer, db.ForeignKey('materia.id'), nullable=True)
    conteudo_id = db.Column(db.Integer, db.ForeignKey('conteudo.id'), nullable=True)
    materia = db.relationship('Materia', foreign_keys=[materia_id])
    conteudo = db.relationship('Conteudo', foreign_keys=[conteudo_id])
    enunciado = db.Column(db.Text, nullable=False)
    texto_base = db.Column(db.Text, nullable=True)
    alternativa_a = db.Column(db.Text, nullable=False)
    alternativa_b = db.Column(db.Text, nullable=False)
    alternativa_c = db.Column(db.Text, nullable=False)
    alternativa_d = db.Column(db.Text, nullable=False)
    alternativa_e = db.Column(db.Text, nullable=False)
    gabarito = db.Column(db.String(1), nullable=False)
    explicacao = db.Column(db.Text, nullable=False)
    ativo = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    answers = db.relationship('Answer', backref='question', lazy=True, cascade='all, delete-orphan')
    comments = db.relationship('Comment', backref='question', lazy=True, cascade='all, delete-orphan')

class PaymentRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    plano = db.Column(db.String(20), nullable=False)
    valor = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), default='PENDENTE', nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    processed_at = db.Column(db.DateTime, nullable=True)
    user = db.relationship('User', backref=db.backref('payment_requests', lazy=True, cascade='all, delete-orphan'))

class Feedback(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    nota = db.Column(db.Integer, nullable=False, default=5)
    texto = db.Column(db.Text, nullable=False)
    aprovado = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Answer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    question_id = db.Column(db.Integer, db.ForeignKey('question.id'), nullable=False)
    resposta = db.Column(db.String(1), nullable=False)
    correta = db.Column(db.Boolean, nullable=False)
    xp_ganho = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Comment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    question_id = db.Column(db.Integer, db.ForeignKey('question.id'), nullable=False)
    texto = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


def current_level(xp):
    level = LEVELS[0][0]
    next_xp = LEVELS[1][1]
    for i, (name, threshold) in enumerate(LEVELS):
        if xp >= threshold:
            level = name
            next_xp = LEVELS[i + 1][1] if i + 1 < len(LEVELS) else threshold
    return level, next_xp


def xp_progress(xp):
    level, next_xp = current_level(xp)
    previous = next(threshold for name, threshold in LEVELS if name == level)
    if next_xp == previous:
        return 100
    return min(100, round((xp - previous) / (next_xp - previous) * 100))


def get_user():
    user_id = session.get('user_id')
    return db.session.get(User, user_id) if user_id else None

def plan_name(user):
    return PLANS.get(user.plano, PLANS['FREE'])

def daily_answer_count(user_id):
    start = datetime.combine(date.today(), time.min)
    return Answer.query.filter(Answer.user_id == user_id, Answer.created_at >= start).count()

def daily_remaining(user):
    if user.plano != 'FREE':
        return None
    return max(0, FREE_DAILY_LIMIT - daily_answer_count(user.id))

def can_access_category(user, categoria):
    return categoria in PLAN_ACCESS.get(user.plano, PLAN_ACCESS['FREE'])

def enforce_question_access(user, categoria=None):
    if categoria and not can_access_category(user, categoria):
        flash(f'Seu plano {plan_name(user)} não inclui questões de {categoria}. Acesse Planos para conhecer as opções Elite.', 'warning')
        return False
    remaining = daily_remaining(user)
    if remaining is not None and remaining <= 0:
        flash(f'Você atingiu o limite gratuito de {FREE_DAILY_LIMIT} questões hoje. O limite será renovado amanhã.', 'warning')
        return False
    return True


def current_streak(user_id):
    answers = Answer.query.filter_by(user_id=user_id).order_by(Answer.id.desc()).all()
    streak = 0
    for answer in answers:
        if answer.correta:
            streak += 1
        else:
            break
    return streak


def stats_for_user(user_id):
    answers = Answer.query.filter_by(user_id=user_id).all()
    total = len(answers)
    acertos = sum(1 for a in answers if a.correta)
    erros = total - acertos
    aproveitamento = round(acertos / total * 100, 1) if total else 0
    streak = current_streak(user_id)
    return {'total': total, 'acertos': acertos, 'erros': erros, 'aproveitamento': aproveitamento, 'streak': streak}


def latest_wrong_questions(user_id):
    answers = Answer.query.filter_by(user_id=user_id).order_by(Answer.id.desc()).all()
    seen = set()
    result = []
    for answer in answers:
        if answer.question_id in seen:
            continue
        seen.add(answer.question_id)
        if not answer.correta and answer.question and answer.question.ativo:
            result.append(answer.question)
    return result


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        user_id = session.get('user_id')
        if not user_id:
            return redirect(url_for('login'))
        user = db.session.get(User, user_id)
        if user is None:
            session.clear()
            flash('Sua sessão expirou. Faça login novamente.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return wrapper


def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        user = db.session.get(User, session['user_id'])
        if not user or not user.is_admin:
            flash('Acesso restrito ao administrador.', 'danger')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return wrapper

@app.context_processor
def inject_globals():
    user = get_user()
    if user:
        level, next_xp = current_level(user.xp)
        return {'current_user': user, 'level': level, 'next_xp': next_xp,
                'progress': xp_progress(user.xp), 'categories': CATEGORIES, 'plan_name': plan_name(user), 'daily_remaining': daily_remaining(user), 'plans': PLANS, 'plan_prices': PLAN_PRICES}
    return {'current_user': None, 'categories': CATEGORIES}

@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return redirect(url_for('dashboard'))

@app.route('/cadastro', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip().lower()
        telefone = request.form.get('telefone', '').strip()
        password = request.form.get('password', '')
        if len(username) < 3 or len(password) < 6 or not email or not telefone:
            flash('Preencha todos os campos. Usuário deve ter 3+ caracteres e senha 6+ caracteres.', 'danger')
            return render_template('register.html')
        if '@' not in email:
            flash('Informe um e-mail válido.', 'danger'); return render_template('register.html')
        if User.query.filter_by(username=username).first():
            flash('Usuário já existe.', 'danger'); return render_template('register.html')
        if User.query.filter_by(email=email).first():
            flash('Este e-mail já está cadastrado.', 'danger'); return render_template('register.html')
        if User.query.filter_by(telefone=telefone).first():
            flash('Este telefone já está cadastrado.', 'danger'); return render_template('register.html')
        user = User(username=username, email=email, telefone=telefone,
                    password_hash=generate_password_hash(password))
        db.session.add(user); db.session.commit()
        session['user_id'] = user.id
        return redirect(url_for('dashboard'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        login_value = request.form.get('login', '').strip().lower()
        user = User.query.filter((User.username == login_value) | (User.email == login_value)).first()
        if user and check_password_hash(user.password_hash, request.form.get('password', '')):
            session['user_id'] = user.id
            return redirect(url_for('dashboard'))
        flash('Usuário/e-mail ou senha inválidos.', 'danger')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear(); return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    user = get_user()
    if user is None:
        session.clear(); flash('Usuário não encontrado. Faça login novamente.', 'warning'); return redirect(url_for('login'))
    stats = stats_for_user(user.id)
    questoes = Question.query.filter_by(ativo=True).count()
    sd = Question.query.filter_by(ativo=True, categoria='SD').count()
    cfo = Question.query.filter_by(ativo=True, categoria='CFO').count()
    return render_template('dashboard.html', questoes=questoes, sd=sd, cfo=cfo, levels=LEVELS, stats=stats, daily_count=daily_answer_count(user.id))

@app.route('/api/materias/<categoria>')
@login_required
def api_materias(categoria):
    categoria = categoria.upper()
    if categoria not in CATEGORY_VALUES:
        return jsonify({'materias': [], 'erro': 'Divisão inválida.'}), 400
    materias = Materia.query.filter_by(categoria=categoria).order_by(Materia.ordem, Materia.id).all()
    return jsonify({'materias': [
        {'id': m.id, 'nome': m.nome, 'ordem': m.ordem}
        for m in materias
    ]})


@app.route('/api/conteudos/<int:materia_id>')
@login_required
def api_conteudos(materia_id):
    materia = db.session.get(Materia, materia_id)
    if not materia:
        return jsonify({'conteudos': [], 'erro': 'Matéria não encontrada.'}), 404
    conteudos = Conteudo.query.filter_by(materia_id=materia_id).order_by(Conteudo.ordem, Conteudo.id).all()
    return jsonify({'conteudos': [
        {'id': c.id, 'nome': c.nome, 'ordem': c.ordem}
        for c in conteudos
    ]})



def edital_options(categoria=None):
    """Return edital subjects for a category, ordered as seeded from the edital."""
    query = Materia.query
    if categoria:
        query = query.filter_by(categoria=categoria.upper())
    return query.order_by(Materia.ordem, Materia.id).all()


def validate_edital_selection(categoria, materia_id, conteudo_id):
    """Validate that the selected subject/content belong to the selected division."""
    categoria = (categoria or '').strip().upper()
    if categoria not in CATEGORY_VALUES:
        raise ValueError('Selecione uma divisão válida: SD ou CFO.')

    try:
        materia_pk = int(materia_id)
        conteudo_pk = int(conteudo_id)
    except (TypeError, ValueError):
        raise ValueError('Selecione uma matéria e um conteúdo do edital.')

    materia = db.session.get(Materia, materia_pk)
    if not materia or materia.categoria != categoria:
        raise ValueError('A matéria selecionada não pertence à divisão escolhida.')

    conteudo = db.session.get(Conteudo, conteudo_pk)
    if not conteudo or conteudo.materia_id != materia.id:
        raise ValueError('O conteúdo selecionado não pertence à matéria escolhida.')

    return materia, conteudo

@app.route('/questoes')
@login_required
def questions():
    categoria = request.args.get('categoria', '').upper()
    materia_id = request.args.get('materia_id', '')
    conteudo_id = request.args.get('conteudo_id', '')
    banca = request.args.get('banca', '')
    ano = request.args.get('ano', '')
    busca = request.args.get('busca', '').strip()
    q = Question.query.filter_by(ativo=True)
    if categoria in CATEGORY_VALUES: q = q.filter_by(categoria=categoria)
    if materia_id.isdigit(): q = q.filter_by(materia_id=int(materia_id))
    if conteudo_id.isdigit(): q = q.filter_by(conteudo_id=int(conteudo_id))
    if banca: q = q.filter_by(banca=banca)
    if ano.isdigit(): q = q.filter_by(ano=int(ano))
    if busca:
        like = f'%{busca}%'
        q = q.filter(db.or_(Question.enunciado.ilike(like), Question.assunto.ilike(like), Question.banca.ilike(like)))
    items = q.order_by(Question.id.desc()).all()
    materias = edital_options(categoria) if categoria in CATEGORY_VALUES else Materia.query.order_by(Materia.categoria, Materia.ordem).all()
    conteudos = Conteudo.query.filter_by(materia_id=int(materia_id)).order_by(Conteudo.ordem).all() if materia_id.isdigit() else []
    return render_template('questions.html', questions=items, categoria=categoria, busca=busca,
        disciplinas=[x[0] for x in db.session.query(Question.disciplina).distinct().order_by(Question.disciplina).all()],
        materias=materias, conteudos=conteudos, bancas=[x[0] for x in db.session.query(Question.banca).distinct().order_by(Question.banca).all()],
        anos=[x[0] for x in db.session.query(Question.ano).distinct().order_by(Question.ano.desc()).all()])

@app.route('/questao/<int:question_id>', methods=['GET', 'POST'])
@login_required
def question(question_id):
    q = db.session.get(Question, question_id)
    if not q or not q.ativo: return 'Questão não encontrada', 404
    user = get_user()
    if not enforce_question_access(user, q.categoria):
        return redirect(url_for('plans'))
    previous = Answer.query.filter_by(user_id=session['user_id'], question_id=q.id).order_by(Answer.id.desc()).first()
    if request.method == 'POST':
        resposta = request.form.get('resposta', '').upper()
        if resposta not in 'ABCDE':
            flash('Selecione uma alternativa.', 'warning'); return redirect(url_for('question', question_id=q.id))
        correta = resposta == q.gabarito.upper()
        xp = 0
        bonus = 0
        if correta:
            streak_before = current_streak(user.id)
            new_streak = streak_before + 1
            xp = BASE_XP_ACERTO
            if new_streak % 10 == 0:
                bonus = BONUS_10_ACERTOS
            elif new_streak % 5 == 0:
                bonus = BONUS_5_ACERTOS
            xp += bonus
            user.xp += xp
        ans = Answer(user_id=user.id, question_id=q.id, resposta=resposta, correta=correta, xp_ganho=xp)
        db.session.add(ans); db.session.commit(); previous = ans
        session['last_bonus'] = bonus
        return render_template('question.html', q=q, previous=previous, submitted=True, streak=current_streak(user.id), bonus=bonus)
    return render_template('question.html', q=q, previous=previous, submitted=False, streak=current_streak(session['user_id']), bonus=0)

@app.route('/questao/<int:question_id>/comentario', methods=['POST'])
@login_required
def add_comment(question_id):
    text_value = request.form.get('texto', '').strip()
    if text_value:
        db.session.add(Comment(user_id=session['user_id'], question_id=question_id, texto=text_value)); db.session.commit()
    return redirect(url_for('question', question_id=question_id))

@app.route('/planos')
@login_required
def plans():
    user = get_user()
    pending_payments = PaymentRequest.query.filter_by(user_id=user.id, status='PENDENTE').order_by(PaymentRequest.created_at.desc()).all()
    return render_template('plans.html', plans=PLANS, daily_limit=FREE_DAILY_LIMIT, plan_prices=PLAN_PRICES, pending_payments=pending_payments, whatsapp_configured=bool(WHATSAPP_NUMBER))

@app.route('/planos/comprar/<plano>', methods=['POST'])
@login_required
def request_plan_purchase(plano):
    user = get_user()
    if plano not in ('ELITE_SD', 'ELITE_CFO', 'ELITE_PRO'):
        flash('Plano inválido.', 'danger')
        return redirect(url_for('plans'))
    if user.plano == plano:
        flash('Você já está neste plano.', 'info')
        return redirect(url_for('plans'))
    # Evita criar vários pedidos pendentes iguais para o mesmo usuário.
    pending = PaymentRequest.query.filter_by(user_id=user.id, plano=plano, status='PENDENTE').first()
    if not pending:
        pending = PaymentRequest(user_id=user.id, plano=plano, valor=PLAN_PRICES[plano])
        db.session.add(pending)
        db.session.commit()
    if not WHATSAPP_NUMBER:
        flash('O WhatsApp de vendas ainda não foi configurado. Defina a variável WHATSAPP_NUMBER no ambiente do servidor.', 'warning')
        return redirect(url_for('plans'))
    message = (
        f'Olá! Quero assinar o {PLANS[plano]} do PMBA Questões.\n'
        f'Valor: R$ {PLAN_PRICES[plano]:.2f}'.replace('.', ',') + '\n'
        f'Usuário: {user.username}\n'
        f'E-mail: {user.email}\n'
        f'Pedido: #{pending.id}'
    )
    from urllib.parse import quote
    return redirect(f'https://wa.me/{WHATSAPP_NUMBER}?text={quote(message)}')

@app.route('/admin/pagamentos')
@admin_required
def admin_payments():
    requests = PaymentRequest.query.order_by(PaymentRequest.created_at.desc()).all()
    return render_template('admin/payments.html', requests=requests, plans=PLANS, plan_prices=PLAN_PRICES)

@app.route('/admin/pagamentos/<int:request_id>/aprovar', methods=['POST'])
@admin_required
def admin_approve_payment(request_id):
    item = db.session.get(PaymentRequest, request_id)
    if not item:
        flash('Pedido não encontrado.', 'danger')
        return redirect(url_for('admin_payments'))
    item.status = 'APROVADO'
    item.processed_at = datetime.utcnow()
    item.user.plano = item.plano
    db.session.commit()
    flash(f'Pagamento #{item.id} aprovado. {item.user.username} agora está no {PLANS[item.plano]}.', 'success')
    return redirect(url_for('admin_payments'))

@app.route('/admin/pagamentos/<int:request_id>/recusar', methods=['POST'])
@admin_required
def admin_reject_payment(request_id):
    item = db.session.get(PaymentRequest, request_id)
    if not item:
        flash('Pedido não encontrado.', 'danger')
        return redirect(url_for('admin_payments'))
    item.status = 'RECUSADO'
    item.processed_at = datetime.utcnow()
    db.session.commit()
    flash(f'Pedido #{item.id} marcado como recusado.', 'success')
    return redirect(url_for('admin_payments'))

@app.route('/feedback', methods=['GET', 'POST'])
@login_required
def feedback():
    if request.method == 'POST':
        texto = request.form.get('texto', '').strip()
        try:
            nota = int(request.form.get('nota', 5))
        except ValueError:
            nota = 5
        nota = max(1, min(5, nota))
        if len(texto) < 5:
            flash('Escreva um feedback com pelo menos 5 caracteres.', 'warning')
        else:
            db.session.add(Feedback(user_id=session['user_id'], nota=nota, texto=texto, aprovado=False))
            db.session.commit()
            flash('Obrigado! Seu feedback foi enviado para análise.', 'success')
            return redirect(url_for('feedback'))
    real_feedbacks = Feedback.query.filter_by(aprovado=True).order_by(Feedback.created_at.desc()).limit(12).all()
    demo_feedbacks = [
        {'nome': 'Aluno SD (exemplo)', 'texto': 'A organização das questões facilita muito a revisão.', 'nota': 5},
        {'nome': 'Candidato CFO (exemplo)', 'texto': 'Gostei da separação por categoria e do acompanhamento de desempenho.', 'nota': 5},
        {'nome': 'Aluno PMBA (exemplo)', 'texto': 'O caderno de erros é uma ideia excelente para revisar os pontos fracos.', 'nota': 5},
    ]
    return render_template('feedback.html', real_feedbacks=real_feedbacks, demo_feedbacks=demo_feedbacks)

@app.route('/ranking')
@login_required
def ranking():
    users = User.query.order_by(User.xp.desc(), User.username.asc()).all()
    return render_template('ranking.html', users=users, levels=LEVELS)

# ---------------- RESOLVER / CADERNO / ESTATISTICAS ----------------
@app.route('/resolver', methods=['GET', 'POST'])
@login_required
def resolver():
    if request.method == 'POST':
        categoria = request.form.get('categoria', '').upper()
        user = get_user()
        if not enforce_question_access(user, categoria if categoria in CATEGORY_VALUES else None):
            return redirect(url_for('plans'))
        materia_id = request.form.get('materia_id', '').strip()
        conteudo_id = request.form.get('conteudo_id', '').strip()
        banca = request.form.get('banca', '').strip()
        quantidade = max(1, min(int(request.form.get('quantidade', 10) or 10), 100))
        q = Question.query.filter_by(ativo=True)
        if categoria in CATEGORY_VALUES: q = q.filter_by(categoria=categoria)
        if materia_id.isdigit(): q = q.filter_by(materia_id=int(materia_id))
        if conteudo_id.isdigit(): q = q.filter_by(conteudo_id=int(conteudo_id))
        if banca: q = q.filter_by(banca=banca)
        ids = [x.id for x in q.order_by(db.func.random()).limit(quantidade).all()]
        if not ids:
            flash('Não há questões para os filtros escolhidos.', 'warning'); return redirect(url_for('resolver'))
        session['quiz_ids'] = ids; session['quiz_index'] = 0; session['quiz_correct'] = 0; session['quiz_wrong'] = 0; session['quiz_xp'] = 0; session['quiz_started'] = datetime.utcnow().isoformat()
        return redirect(url_for('resolver_question'))
    categoria = request.args.get('categoria','').upper()
    materia_id = request.args.get('materia_id','')
    materias = edital_options(categoria) if categoria in CATEGORY_VALUES else []
    conteudos = Conteudo.query.filter_by(materia_id=int(materia_id)).order_by(Conteudo.ordem).all() if materia_id.isdigit() else []
    return render_template('resolver.html', categorias=CATEGORIES, materias=materias, conteudos=conteudos,
        bancas=[x[0] for x in db.session.query(Question.banca).distinct().order_by(Question.banca).all()])

@app.route('/resolver/questao', methods=['GET', 'POST'])
@login_required
def resolver_question():
    ids = session.get('quiz_ids', [])
    index = session.get('quiz_index', 0)
    if not ids or index >= len(ids):
        return redirect(url_for('quiz_result'))
    q = db.session.get(Question, ids[index])
    if not q or not q.ativo:
        session['quiz_index'] = index + 1
        return redirect(url_for('resolver_question'))
    result = None
    if request.method == 'POST':
        user = get_user()
        if user.plano == 'FREE' and daily_remaining(user) == 0:
            flash(f'Você atingiu o limite gratuito de {FREE_DAILY_LIMIT} questões hoje.', 'warning')
            return redirect(url_for('plans'))
        if not can_access_category(user, q.categoria):
            flash(f'Seu plano {plan_name(user)} não inclui questões de {q.categoria}.', 'warning')
            return redirect(url_for('plans'))
        resposta = request.form.get('resposta', '').upper()
        if resposta not in 'ABCDE':
            flash('Selecione uma alternativa.', 'warning'); return redirect(url_for('resolver_question'))
        correta = resposta == q.gabarito.upper(); xp = 0; bonus = 0
        if correta:
            new_streak = current_streak(user.id) + 1
            xp = BASE_XP_ACERTO
            if new_streak % 10 == 0: bonus = BONUS_10_ACERTOS
            elif new_streak % 5 == 0: bonus = BONUS_5_ACERTOS
            xp += bonus; user.xp += xp; session['quiz_correct'] = session.get('quiz_correct', 0) + 1
        else:
            session['quiz_wrong'] = session.get('quiz_wrong', 0) + 1
        db.session.add(Answer(user_id=user.id, question_id=q.id, resposta=resposta, correta=correta, xp_ganho=xp)); db.session.commit()
        session['quiz_xp'] = session.get('quiz_xp', 0) + xp
        result = {'correta': correta, 'resposta': resposta, 'xp': xp, 'bonus': bonus}
    return render_template('resolver_question.html', q=q, index=index, total=len(ids), result=result,
                           correct=session.get('quiz_correct', 0), wrong=session.get('quiz_wrong', 0))

@app.route('/resolver/proxima', methods=['POST'])
@login_required
def resolver_next():
    session['quiz_index'] = session.get('quiz_index', 0) + 1
    return redirect(url_for('resolver_question'))

@app.route('/simulado')
@login_required
def simulado():
    return render_template('simulado.html')

@app.route('/simulado/iniciar', methods=['POST'])
@login_required
def simulado_start():
    user = get_user()
    quantidade = max(5, min(int(request.form.get('quantidade', 20) or 20), 100))
    categorias = request.form.getlist('categoria')
    if categorias:
        categorias = [c for c in categorias if c in CATEGORY_VALUES]
        for cat in categorias:
            if not can_access_category(user, cat):
                flash(f'Seu plano {plan_name(user)} não inclui {cat}.', 'warning')
                return redirect(url_for('plans'))
    if user.plano == 'FREE' and daily_remaining(user) is not None and daily_remaining(user) < quantidade:
        flash(f'No plano gratuito você tem {daily_remaining(user)} questão(ões) restantes hoje. Reduza o simulado ou conheça os planos Elite.', 'warning')
        return redirect(url_for('plans'))
    q = Question.query.filter_by(ativo=True)
    if categorias:
        categorias = [c for c in categorias if c in CATEGORY_VALUES]
        if categorias: q = q.filter(Question.categoria.in_(categorias))
    ids = [x.id for x in q.order_by(db.func.random()).limit(quantidade).all()]
    if not ids:
        flash('Não há questões suficientes para o simulado.', 'warning'); return redirect(url_for('simulado'))
    session['quiz_ids'] = ids; session['quiz_index'] = 0; session['quiz_correct'] = 0; session['quiz_wrong'] = 0; session['quiz_xp'] = 0; session['simulado'] = True
    return redirect(url_for('resolver_question'))

@app.route('/simulado/finalizar', methods=['POST'])
@login_required
def simulado_finish():
    session['quiz_index'] = len(session.get('quiz_ids', []))
    return redirect(url_for('quiz_result'))

@app.route('/quiz/resultado')
@login_required
def quiz_result():
    total = len(session.get('quiz_ids', [])); correct = session.get('quiz_correct', 0); wrong = session.get('quiz_wrong', 0); xp = session.get('quiz_xp', 0)
    if total == 0: return redirect(url_for('resolver'))
    percent = round(correct / total * 100, 1) if total else 0
    simulated = session.get('simulado', False)
    xp_display = xp
    if simulated and total:
        user = get_user()
        user.xp += 100
        db.session.commit()
        xp_display += 100
    data = {'total': total, 'correct': correct, 'wrong': wrong, 'percent': percent, 'xp': xp_display, 'simulado': simulated}
    for key in ['quiz_ids','quiz_index','quiz_correct','quiz_wrong','quiz_xp','quiz_started','simulado']:
        session.pop(key, None)
    return render_template('quiz_result.html', data=data)

@app.route('/caderno-erros')
@login_required
def error_notebook():
    items = latest_wrong_questions(session['user_id'])
    return render_template('error_notebook.html', questions=items)

@app.route('/estatisticas')
@login_required
def statistics():
    user = get_user(); stats = stats_for_user(user.id)
    rows = []
    for disciplina, in db.session.query(Question.disciplina).distinct().order_by(Question.disciplina).all():
        ans = Answer.query.join(Question).filter(Answer.user_id == user.id, Question.disciplina == disciplina).all()
        total = len(ans); acertos = sum(a.correta for a in ans)
        rows.append({'disciplina': disciplina, 'total': total, 'acertos': acertos, 'erros': total-acertos, 'percent': round(acertos/total*100,1) if total else 0})
    return render_template('statistics.html', stats=stats, rows=rows)

# ---------------- ADMIN ----------------
@app.route('/admin')
@admin_required
def admin():
    return render_template('admin/dashboard.html',
        total_questions=Question.query.count(), active_questions=Question.query.filter_by(ativo=True).count(),
        sd_questions=Question.query.filter_by(categoria='SD').count(), cfo_questions=Question.query.filter_by(categoria='CFO').count(),
        total_users=User.query.count(), total_answers=Answer.query.count(), feedback_pending=Feedback.query.filter_by(aprovado=False).count(), payment_pending=PaymentRequest.query.filter_by(status='PENDENTE').count(), plan_counts={p: User.query.filter_by(plano=p).count() for p in PLANS})

@app.route('/admin/questoes')
@admin_required
def admin_questions():
    categoria = request.args.get('categoria', '').upper(); busca = request.args.get('busca', '').strip(); status = request.args.get('status', '')
    materia_id = request.args.get('materia_id', ''); conteudo_id = request.args.get('conteudo_id', '')
    q = Question.query
    if categoria in CATEGORY_VALUES: q = q.filter_by(categoria=categoria)
    if materia_id.isdigit(): q = q.filter_by(materia_id=int(materia_id))
    if conteudo_id.isdigit(): q = q.filter_by(conteudo_id=int(conteudo_id))
    if status == 'ativas': q = q.filter_by(ativo=True)
    elif status == 'inativas': q = q.filter_by(ativo=False)
    if busca:
        like = f'%{busca}%'; q = q.filter(db.or_(Question.enunciado.ilike(like), Question.assunto.ilike(like), Question.banca.ilike(like)))
    items = q.order_by(Question.id.desc()).all()
    materias = edital_options(categoria) if categoria in CATEGORY_VALUES else []
    conteudos = Conteudo.query.filter_by(materia_id=int(materia_id)).order_by(Conteudo.ordem).all() if materia_id.isdigit() else []
    return render_template('admin/questions.html', questions=items, categoria=categoria, busca=busca, status=status,
                           materias=materias, conteudos=conteudos, materia_id=materia_id, conteudo_id=conteudo_id)

def question_form_data(form):
    categoria = form.get('categoria', '').upper()
    banca = form.get('banca', '').strip()
    ano_raw = form.get('ano', '').strip()
    materia_id = form.get('materia_id', '').strip()
    conteudo_id = form.get('conteudo_id', '').strip()
    enunciado = form.get('enunciado', '').strip()
    texto_base = form.get('texto_base', '').strip() or None
    alternativas = {k: form.get(f'alternativa_{k}', '').strip() for k in 'abcde'}
    gabarito = form.get('gabarito', '').upper()
    explicacao = form.get('explicacao', '').strip()

    if not banca or not enunciado or any(not v for v in alternativas.values()) or gabarito not in 'ABCDE' or not explicacao:
        raise ValueError('Preencha todos os campos obrigatórios e escolha um gabarito entre A e E.')
    try:
        ano = int(ano_raw)
    except ValueError:
        raise ValueError('Informe um ano válido.')
    if ano < 1900 or ano > 2100:
        raise ValueError('Informe um ano entre 1900 e 2100.')

    if not materia_id:
        legacy_materia = form.get('disciplina', '').strip()
        materia_obj = Materia.query.filter_by(categoria=categoria, nome=legacy_materia).first()
        materia_id = materia_obj.id if materia_obj else ''
    if not conteudo_id:
        legacy_conteudo = form.get('assunto', '').strip()
        if materia_id and legacy_conteudo:
            conteudo_obj = Conteudo.query.filter_by(materia_id=int(materia_id), nome=legacy_conteudo).first()
            conteudo_id = conteudo_obj.id if conteudo_obj else ''
    materia, conteudo = validate_edital_selection(categoria, materia_id, conteudo_id)
    return dict(categoria=categoria, banca=banca, ano=ano,
                disciplina=materia.nome, assunto=conteudo.nome,
                materia_id=materia.id, conteudo_id=conteudo.id,
                enunciado=enunciado, texto_base=texto_base,
                alternativa_a=alternativas['a'], alternativa_b=alternativas['b'],
                alternativa_c=alternativas['c'], alternativa_d=alternativas['d'],
                alternativa_e=alternativas['e'], gabarito=gabarito,
                explicacao=explicacao)


@app.route('/admin/questoes/nova', methods=['GET', 'POST'])
@admin_required
def admin_new_question():
    if request.method == 'POST':
        try:
            q = Question(**question_form_data(request.form)); db.session.add(q); db.session.commit(); flash(f'Questão #{q.id} cadastrada com sucesso.', 'success'); return redirect(url_for('admin_questions'))
        except ValueError as exc: flash(str(exc), 'danger')
        except Exception as exc:
            db.session.rollback()
            app.logger.exception('Erro ao cadastrar questão: %s', exc)
            flash('Não foi possível cadastrar a questão. Verifique os campos e tente novamente.', 'danger')
    return render_template('admin/new_question.html', q=None, form_title='Cadastrar questão', submit_label='Cadastrar questão', edital_materias=Materia.query.filter_by(categoria='SD').order_by(Materia.ordem).all())

@app.route('/admin/questoes/<int:question_id>/editar', methods=['GET', 'POST'])
@admin_required
def admin_edit_question(question_id):
    q = db.session.get(Question, question_id)
    if not q: flash('Questão não encontrada.', 'danger'); return redirect(url_for('admin_questions'))
    if request.method == 'POST':
        try:
            data = question_form_data(request.form)
            for key, value in data.items(): setattr(q, key, value)
            db.session.commit(); flash(f'Questão #{q.id} atualizada.', 'success'); return redirect(url_for('admin_questions'))
        except ValueError as exc: flash(str(exc), 'danger')
        except Exception as exc:
            db.session.rollback()
            app.logger.exception('Erro ao atualizar questão: %s', exc)
            flash('Não foi possível atualizar a questão. Verifique os campos e tente novamente.', 'danger')
    return render_template('admin/new_question.html', q=q, form_title=f'Editar questão #{q.id}', submit_label='Salvar alterações', edital_materias=Materia.query.filter_by(categoria=q.categoria).order_by(Materia.ordem).all())

@app.route('/admin/questoes/<int:question_id>/alternar', methods=['POST'])
@admin_required
def admin_toggle_question(question_id):
    q = db.session.get(Question, question_id)
    if not q: flash('Questão não encontrada.', 'danger'); return redirect(url_for('admin_questions'))
    q.ativo = not q.ativo; db.session.commit(); flash('Status da questão atualizado.', 'success'); return redirect(url_for('admin_questions'))

@app.route('/admin/questoes/<int:question_id>/excluir', methods=['POST'])
@admin_required
def admin_delete_question(question_id):
    q = db.session.get(Question, question_id)
    if not q: flash('Questão não encontrada.', 'danger'); return redirect(url_for('admin_questions'))
    db.session.delete(q); db.session.commit(); flash(f'Questão #{question_id} excluída.', 'success'); return redirect(url_for('admin_questions'))

@app.route('/admin/importar', methods=['GET', 'POST'])
@admin_required
def admin_import():
    if request.method == 'POST':
        file = request.files.get('arquivo')
        if not file or not file.filename.lower().endswith('.csv'): flash('Envie um arquivo CSV.', 'danger'); return redirect(url_for('admin_import'))
        try:
            content = file.read().decode('utf-8-sig'); reader = csv.DictReader(io.StringIO(content), delimiter=',')
            required = {'categoria','banca','ano','disciplina','assunto','texto_base','enunciado','alternativa_a','alternativa_b','alternativa_c','alternativa_d','alternativa_e','gabarito','explicacao'}
            if not reader.fieldnames or not required.issubset(set(reader.fieldnames)): flash('CSV inválido. Use o modelo disponível nesta página.', 'danger'); return redirect(url_for('admin_import'))
            created = 0
            for row in reader: db.session.add(Question(**question_form_data(row))); created += 1
            db.session.commit(); flash(f'{created} questão(ões) importada(s) com sucesso.', 'success'); return redirect(url_for('admin_questions'))
        except Exception as exc: db.session.rollback(); flash(f'Falha na importação: {exc}', 'danger')
    return render_template('admin/import.html')

@app.route('/admin/usuarios')
@admin_required
def admin_users():
    users = User.query.order_by(User.xp.desc(), User.username.asc()).all()
    return render_template('admin/users.html', users=users, levels=LEVELS, plans=PLANS)

@app.route('/admin/usuarios/<int:user_id>/plano', methods=['POST'])
@admin_required
def admin_set_plan(user_id):
    user = db.session.get(User, user_id)
    plano = request.form.get('plano', 'FREE')
    if not user or plano not in PLANS:
        flash('Usuário ou plano inválido.', 'danger')
        return redirect(url_for('admin_users'))
    user.plano = plano
    db.session.commit()
    flash(f'Plano de {user.username} atualizado para {PLANS[plano]}.', 'success')
    return redirect(url_for('admin_users'))

@app.route('/admin/feedbacks')
@admin_required
def admin_feedbacks():
    feedbacks = Feedback.query.order_by(Feedback.created_at.desc()).all()
    return render_template('admin/feedbacks.html', feedbacks=feedbacks)

@app.route('/admin/feedbacks/<int:feedback_id>/aprovar', methods=['POST'])
@admin_required
def admin_approve_feedback(feedback_id):
    item = db.session.get(Feedback, feedback_id)
    if not item:
        flash('Feedback não encontrado.', 'danger')
    else:
        item.aprovado = True
        db.session.commit()
        flash('Feedback aprovado.', 'success')
    return redirect(url_for('admin_feedbacks'))


@app.route('/admin/questoes/<int:question_id>/estatisticas')
@admin_required
def admin_question_stats(question_id):
    q = db.session.get(Question, question_id)
    if not q: flash('Questão não encontrada.', 'danger'); return redirect(url_for('admin_questions'))
    answers = Answer.query.filter_by(question_id=q.id).all(); total=len(answers); correct=sum(a.correta for a in answers)
    percent=round(correct/total*100,1) if total else 0
    return render_template('admin/question_stats.html', q=q, total=total, correct=correct, wrong=total-correct, percent=percent)


SD_EDITAL = {
'Língua Portuguesa': [
'Compreensão e interpretação de textos.','Tipologia textual e gêneros textuais.','Ortografia oficial.','Acentuação gráfica.','Classes de palavras.','Uso do sinal indicativo de crase.','Sintaxe da oração e do período.','Pontuação.','Concordância nominal e verbal.','Regência nominal e verbal.','Significação das palavras.'
],
'História do Brasil': [
'Descobrimento do Brasil (1500).','Brasil Colônia (1530-1815): Capitanias Hereditárias, Economia, Extrativismo Vegetal, Extrativismo Mineral, Pecuária, Escravidão, Organização Político-Administrativa, Expansão Territorial.','Independência do Brasil (1822): a Nomeação do Príncipe Regente D. Pedro I, Dia do Fico, Reconhecimento da Independência do Brasil.','Primeiro Reinado (1822-1831).','Segundo Reinado (1831-1840).','Primeira República (1889-1930): o Primeiro Governo Provisório, Assembleia Constituinte, Presidência de Deodoro da Fonseca, a Política dos Governadores, o Coronelismo, Movimentos Tenentistas, Coluna Prestes, Revolta da Armada.','Revolução de 1930.','Era Vargas (1930-1945).','Os Presidentes do Brasil de 1964 à atualidade.','História da Bahia.','Independência da Bahia.','Revolta de Canudos.','Revolta dos Malês.','Conjuração Baiana.','Sabinada.'
],
'Geografia do Brasil': [
'Relevo brasileiro.','Urbanização: crescimento urbano, problemas estruturais, contingente populacional brasileiro.','Tipos de fontes de energia que participam da matriz energética brasileira: eólica, hidráulica, biomassa, solar e a das marés.','Problemas Ambientais.','Clima: pressão atmosférica, umidade, temperatura, fatores que determinam o clima, mudanças climáticas e as suas consequências.','Geografia da Bahia: aspectos políticos, físicos, econômicos, sociais e culturais.'
],
'Matemática': [
'Conjuntos numéricos: Números Naturais, Inteiros, Racionais, Reais e Complexos (forma algébrica e forma trigonométrica). Operações, propriedades e aplicações. Sequências numéricas, progressão aritmética e progressão geométrica.','Álgebra: Expressões algébricas. Polinômios: operações e propriedades. Equações polinomiais e inequações relacionadas.','Funções: generalidades. Funções elementares: 1º grau, 2º grau, modular, exponencial e logarítmica, gráficos. Propriedades.','Sistemas lineares, Matrizes e Determinantes: Propriedades, aplicações.','Análise Combinatória: Arranjos, Permutações e Combinações simples, Binômio de Newton e Probabilidade em espaços amostrais finitos.','Geometria e Medidas: Geometria plana: figuras geométricas, congruência, semelhança, perímetro e área. Geometria espacial: paralelismo, perpendicularismo entre retas e planos, áreas e volumes dos sólidos geométricos: prisma, pirâmide, cilindro, cone e esfera. Geometria analítica no plano: retas, circunferência e distâncias.','Trigonometria: razões trigonométricas, funções, fórmulas de transformações trigonométricas, equações e triângulos.'
],
'Atualidades': [
'Globalização: conceitos, efeitos e implicações sociais, econômicas, políticas e culturais.','Multiculturalidade, Pluralidade e Diversidade Cultural.','Tecnologias de Informação e Comunicação: conceitos, efeitos e implicações sociais, econômicas, políticas e culturais.'
],
'Informática': [
'Conceitos e modos de utilização de aplicativos para edição de textos (Word, Writer), planilhas (Excel, Calc), apresentações (PowerPoint, Impress); Microsoft Office (versão 2007 e superiores), LibreOffice (versão 5.0 e superiores).','Sistemas operacionais Windows 7, Windows 10 e Linux.','Organização e gerenciamento de informações, arquivos, pastas e programas.','Atalhos de teclado, ícones, área de trabalho e lixeira.','Conceitos básicos e modos de utilização de tecnologias, ferramentas, aplicativos e procedimentos associados à Internet e intranet.','Correio eletrônico.','Computação em nuvem.'
],
'Direito Constitucional': [
'Constituição da República Federativa do Brasil: Dos princípios fundamentais.','Constituição da República Federativa do Brasil: Dos Direitos e garantias fundamentais.','Constituição da República Federativa do Brasil: Da organização do Estado.','Constituição da República Federativa do Brasil: Da Administração Pública.','Constituição da República Federativa do Brasil: Dos militares dos Estados, do Distrito Federal e dos Territórios.','Constituição da República Federativa do Brasil: Da Segurança Pública.','Constituição do Estado da Bahia: Dos princípios fundamentais.','Constituição do Estado da Bahia: Direitos e garantias fundamentais.','Constituição do Estado da Bahia: Dos Servidores Públicos Militares.','Constituição do Estado da Bahia: Da Segurança Pública.'
],
'Direitos Humanos': [
'A Declaração Universal dos Direitos Humanos/1948.','Convenção Americana sobre Direitos Humanos/1969 (Pacto de São José da Costa Rica) (art. 1° ao 32).','Pacto Internacional dos Direitos Econômicos, Sociais e Culturais (art. 1° ao 15).','Declaração de Pequim Adotada pela Quarta Conferência Mundial sobre as Mulheres: Ação para Igualdade, Desenvolvimento e Paz.'
],
'Direito Administrativo': [
'Administração Pública.','Princípios fundamentais da administração pública.','Poderes e deveres dos administradores públicos: uso e abuso do poder, poderes vinculado, discricionário, hierárquico, disciplinar e regulamentar, poder de polícia, deveres dos administradores públicos.','Servidores públicos: cargo, emprego e função públicos.','Regime jurídico do militar estadual: Estatuto dos Policiais Militares do Estado da Bahia (Lei estadual nº 7.990, de 27 de dezembro de 2001 - arts 1º ao 59).'
],
'Direito Penal': [
'Do crime: Elementos.','Consumação e tentativa.','Desistência voluntária e arrependimento eficaz.','Arrependimento posterior.','Crime impossível.','Causas de exclusão de ilicitude e culpabilidade.','Contravenção.','Dos crimes contra a vida (homicídio, lesão corporal, rixa).','Dos crimes contra a liberdade pessoal (constrangimento ilegal, ameaça, perseguição, sequestro e cárcere privado).','Dos crimes contra o patrimônio (furto, roubo, extorsão, apropriação indébita, receptação).','Dos crimes contra a dignidade sexual (estupro, importunação sexual, assédio sexual).','Corrupção ativa.','Corrupção passiva.','Lei n° 9.455, de 07 de abril de 1997 (Crimes de tortura).'
],
'Igualdade Racial e de Gênero': [
'Constituição da República Federativa do Brasil (art. 1°, 3°, 4° e 5°).','Constituição do Estado da Bahia, (Cap. XXIII “Do Negro”).','Lei n° 12.288, de 20 de julho de 2010 (Estatuto da Igualdade Racial).','Lei nº 7.716, de 5 de janeiro de 1989 e Lei n° 9.459, de 13 de maio de 1997 (crimes resultantes de preconceito de raça ou de cor).','Decreto n° 65.810, de 08 de dezembro de 1969 (Convenção internacional sobre a eliminação de todas as formas de discriminação racial).','Decreto n° 4.377, de 13 de setembro de 2002 (Convenção sobre a eliminação de todas as formas de discriminação contra a mulher).','Lei nº 11.340, de 7 de agosto de 2006 (Lei Maria da Penha).','Código Penal Brasileiro (art. 140).','Lei n° 9.455, de 7 de abril de 1997 (Crime de Tortura).','Lei nº 7.437, de 20 de dezembro de 1985 (Lei Caó).','Lei Estadual n° 10.549, de 28 de dezembro de 2006 (Secretaria de Promoção da Igualdade Racial).','Lei nº 10.678, de 23 de maio de 2003 (Secretaria de Políticas de Promoção da Igualdade Racial da Presidência da República).'
],
'Direito Penal Militar': [
'Dos crimes contra a autoridade ou disciplina militar: motim, revolta, conspiração, aliciação para motim ou revolta.','Da violência contra superior ou militar de serviço.','Desrespeito a superior.','Recusa de obediência.','Reunião ilícita.','Publicação ou crítica indevida.','Resistência mediante ameaça ou violência.','Dos crimes contra o serviço militar e o dever militar: deserção, abandono de posto, descumprimento de missão, embriaguez em serviço, dormir em serviço.','Crimes contra a Administração Militar: desacato a superior, desacato a militar, desobediência, peculato, peculato-furto, concussão.','Dos crimes contra o dever funcional: prevaricação.'
]}

CFO_EDITAL = {
'Língua Portuguesa': ['Leitura e interpretação de textos: verbais extraídos de livros e periódicos contemporâneos; mistos (verbais/não verbais) e não verbais; textos publicitários (propagandas, mensagens publicitárias, outdoors, etc).','Nomes e verbo. Flexões nominais e verbais.','Advérbio e suas circunstâncias de tempo, lugar, meio, intensidade, negação, afirmação, dúvida, etc.','Palavras de relação intervocabular e interoracional: preposições e conjunções.','Frase, oração, período. Elementos constituintes da oração: termos essenciais, integrantes e acessórios. Coordenação e Subordinação.','Sintaxe de colocação, concordância e regência. Crase.','Formas de discurso: direto, indireto e indireto livre.','Semântica: sinonímia, antonímia e heteronímia.','Pontuação e seus recursos sintático-semânticos.','Acentuação e ortografia.','Diferença entre redação técnica (oficial) e redação estilística e suas respectivas características.','Correspondência oficial: conceito e tipos de documentos.','Diferença entre ofício e memorando.'],
'Língua Inglesa': ['Compreensão de textos verbais e não-verbais.','Substantivos: Formação do plural: regular, irregular e casos especiais.','Gênero. Contáveis e não-contáveis.','Formas possessivas dos nomes. Modificadores do nome.','Artigos e Demonstrativos: Definidos, indefinidos e outros determinantes. Demonstrativo de acordo com a posição, singular e plural.','Adjetivos: Grau comparativo e superlativo: regulares e irregulares. Indefinidos.','Numerais Cardinais e Ordinais.','Pronomes: Pessoais: sujeito e objeto.','Possessivos: substantivos e adjetivos. Reflexivos. Indefinidos. Interrogativos. Relativos.','Verbos (Modos, tempos e formas): Regulares e irregulares. Auxiliares e impessoais. Modais. Two-word verbs. Voz ativa e voz passiva. O gerúndio e seu uso específico.','Discurso direto e indireto. Sentenças condicionais.','Advérbios: Tipos: frequência, modo, lugar, tempo, intensidade, dúvida, afirmação.','Expressões adverbiais.','Palavras de relação: Preposições. Conjunções.','Derivação de palavras pelos processos de prefixação e sufixação. Semântica / sinonímia e antonímia.'],
'Matemática': ['Conjuntos numéricos: Números Naturais, Inteiros, Racionais, Reais e Complexos (forma algébrica e forma trigonométrica). Operações, propriedades e aplicações. Sequências numéricas, progressão aritmética e progressão geométrica.','Álgebra: Expressões algébricas. Polinômios: operações e propriedades. Equações polinomiais e inequações relacionadas.','Funções: generalidades. Funções elementares: 1º grau, 2º grau, modular, exponencial e logarítmica, gráficos. Propriedades.','Sistemas lineares, Matrizes e Determinantes: Propriedades, aplicações.','Análise Combinatória: Arranjos, Permutações e Combinações simples, Binômio de Newton e Probabilidade em espaços amostrais finitos.','Geometria e Medidas: Geometria plana: figuras geométricas, congruência, semelhança, perímetro e área. Geometria espacial: paralelismo, perpendicularismo entre retas e planos, áreas e volumes dos sólidos geométricos: prisma, pirâmide, cilindro, cone e esfera. Geometria analítica no plano: retas, circunferência e distâncias.','Trigonometria: razões trigonométricas, funções, fórmulas de transformações trigonométricas, equações e triângulos.','Proporcionalidade e Finanças: Grandezas proporcionais: Porcentagem. Acréscimos e descontos. Juros: Capitalização simples e Capitalização composta.','Tratamento da Informação: Estatística: Estatística descritiva, resolução de problemas, tabelas, medidas de tendência central e medidas de dispersão. Gráficos estatísticos usuais.','Resolução de problemas envolvendo frações, conjuntos, porcentagens, sequências (com números, com figuras, de palavras).'],
'Informática': ['Conceitos e modos de utilização de aplicativos para edição de textos (Word, Writer), planilhas (Excel, Calc) e apresentações (PowerPoint, Impress); Microsoft Office (versão 2007 e superiores) e LibreOffice (versão 5.0 e superiores).','Sistemas operacionais Windows 7, Windows 10 e Linux.','Organização e gerenciamento de informações, arquivos, pastas e programas.','Atalhos de teclado, ícones, área de trabalho e lixeira.','Conceitos básicos e modos de utilização de tecnologias, ferramentas, aplicativos e procedimentos associados à Internet e intranet.','Correio eletrônico.','Computação em nuvem.','Certificação e assinatura digital.','Segurança da Informação.','Componentes de um computador.','Dispositivos de armazenamento, processadores, memórias e periféricos.'],
'Ciências Humanas': ['História: Antiguidade.','História: Mundo Medieval.','História: Mundo Moderno.','História: Mundo Contemporâneo.','História: Brasil Colônia.','História: Brasil Império.','História: Brasil República (de 1889 aos dias atuais).','História: Aspectos do desenvolvimento cultural e científico do Brasil no século XX.','História: A globalização e as questões ambientais.','História: História da Bahia.','História: Independência da Bahia.','História: Revolta de Canudos.','História: Revolta dos Malês.','História: Conjuração Baiana.','História: Sabinada.','História: Atualidades.','Geografia: A relação sociedade-natureza; os mecanismos da natureza; os recursos naturais e a sobrevivência do homem.','Geografia: As desigualdades na distribuição e na apropriação dos recursos naturais no mundo; uso dos recursos naturais e preservação do meio ambiente.','Geografia: Estruturação econômica, social e política do espaço mundial; capitalismo, industrialização, transnacionalização do capital; economias industriais e não industriais; transformações na relação cidade-campo; industrialização e desenvolvimento tecnológico; papel do Estado; mobilidade espacial e crescimento demográfico; divisão internacional e territorial do trabalho; fim da Guerra Fria, desagregação da URSS e nova ordem econômica mundial.','Geografia: Processo de ocupação e produção do espaço brasileiro; formação territorial; industrialização brasileira e internacionalização do capital; urbanização, metropolização e qualidade de vida; estrutura e produção agrária e impactos ambientais; população, crescimento, estrutura e migrações, condições de vida e de trabalho; papel do Estado e políticas territoriais; regionalização do Brasil.'],
'Direito Constitucional': ['Constituição da República Federativa do Brasil: Dos princípios fundamentais.','Constituição da República Federativa do Brasil: Dos direitos e garantias fundamentais; dos direitos e deveres individuais e coletivos; da nacionalidade; dos direitos políticos.','Da organização do Estado; da Administração Pública; dos militares dos Estados, do Distrito Federal e dos Territórios.','Da Defesa do Estado e das Instituições Democráticas; das Forças Armadas; da segurança pública.','Constituição do Estado da Bahia: dos servidores públicos militares; do Poder Executivo; disposições gerais; atribuições do Governador do Estado; Justiça Militar; Segurança Pública; Família; Direitos Específicos da Mulher; Criança e Adolescente; Idoso; Deficiente; Negro; Índio.'],
'Direitos Humanos': ['A Declaração Universal dos Direitos Humanos/1948.','Convenção Americana sobre Direitos Humanos/1969 (Pacto de São José da Costa Rica) (arts. 1º ao 32).','Convenção Internacional Sobre a Eliminação de Todas as Formas de Discriminação Racial (Decreto nº 65.810/69).','Convenção Sobre Eliminação de Todas as Formas de Discriminação Contra a Mulher (Decreto nº 4.377/02).','Estatuto da Igualdade Racial e de Combate a Intolerância Religiosa (Lei Estadual nº 13.182/14).'],
'Direito Administrativo': ['Princípios fundamentais da administração pública.','Poderes administrativos: poder vinculado; poder discricionário; poder hierárquico; poder disciplinar; poder regulamentar; poder de polícia; uso e abuso do poder.','Atos administrativos: Conceito; Atributos; Requisitos; Classificação; Extinção.','Organização administrativa: Órgãos públicos: conceito e classificação; Entidades administrativas: conceito e espécies.','Agentes públicos: classificação.','Regime jurídico do militar estadual: Estatuto dos Policiais Militares do Estado da Bahia (Lei Estadual n.º 7.990/01 - arts 1º ao 92).','Lei Geral de Proteção de Dados Pessoais – LGPD (Lei n.º 13.709/2018 – arts 1º ao 32).'],
'Direito Penal': ['Da aplicação da lei penal.','Lei penal no tempo.','Lei penal no espaço.','Do crime: Elementos.','Consumação e tentativa.','Desistência voluntária e arrependimento eficaz.','Arrependimento posterior.','Crime impossível.','Causas de exclusão de ilicitude e culpabilidade.','Dos crimes contra a pessoa (homicídio, feminicídio, lesão corporal, calúnia, difamação e injúria).','Dos crimes contra a liberdade pessoal (constrangimento ilegal, ameaça, sequestro e cárcere privado).','Dos crimes contra o patrimônio (furto, roubo, extorsão, apropriação indébita, receptação).','Dos crimes contra a dignidade sexual (estupro, importunação sexual, assédio sexual, estupro de vulnerável, corrupção de menores).','Dos crimes contra a paz pública (incitação ao crime, apologia de crime ou criminoso).','Dos crimes contra a administração pública (peculato e suas formas, concussão, corrupção passiva, prevaricação, condescendência criminosa, resistência, desobediência, desacato, corrupção ativa, contrabando).'],
'Direito Processual Penal': ['Princípios do Processo Penal.','Inquérito Policial.','Da Prova: conceito, finalidade e obrigatoriedade; do exame de corpo de delito.','Da Prisão (arts 283 a 309 do CPP).','Lei das Contravenções Penais (Decreto-Lei n.º 3.688/41).','Contravenções penais: a prática de atos resultantes de preconceito de raça, de cor, de sexo ou de estado civil (Lei nº 7.437/85).','Lei nº 13.869/19: Das sanções de natureza civil e administrativa; Dos crimes e das penas.','Estatuto da Criança e do Adolescente (Lei n.º 8.069/90 – arts. 1º ao 6º; 15 a 18-B; 98 a 130; 225 a 258).','Lei que define os crimes resultantes de preconceito de raça ou de cor (Lei nº 7.716/89).','Estatuto da Pessoa com Deficiência (Lei nº 13.146/15 – arts 1º a 13; 88 a 91).','Crimes de Tortura (Lei n.º 9.455/97).','Estatuto do Idoso (Lei n.º 10.741/03 – arts 1º ao 10).','Lei Maria da Penha (Lei n.º 11.340/06).','Lei que institui o sistema nacional de políticas públicas sobre drogas (Lei n.º 11.343/06 – arts 1º ao 4º, 33 ao 39).'],
'Direito Penal Militar': ['Dos crimes militares em tempo de paz.','Dos crimes contra a autoridade ou disciplina militar: motim, revolta, aliciação e incitamento; violência contra superior ou militar de serviço; desrespeito a superior e a símbolo nacional ou à farda; insubordinação; resistência.','Dos crimes contra o serviço militar e o dever militar: insubmissão; criação ou simulação de incapacidade física; deserção (arts 187 a 194); abandono de posto; descumprimento de missão; embriaguez em serviço; dormir em serviço.','Dos crimes contra a Administração Militar: desacato e desobediência.'],
'Direito Processual Penal Militar': ['Do Inquérito Policial Militar.','Da prisão em flagrante.','Da deserção em geral.','Do processo de deserção do oficial.','Do processo de deserção de praça com ou sem graduação e de praça especial.']
}

@app.cli.command('seed-edital')
def seed_edital():
    db.create_all()
    for categoria, dataset in [('SD', SD_EDITAL), ('CFO', CFO_EDITAL)]:
        for ordem, (nome, conteudos) in enumerate(dataset.items(), 1):
            materia = Materia.query.filter_by(categoria=categoria, nome=nome).first()
            if not materia:
                materia = Materia(categoria=categoria, nome=nome, ordem=ordem); db.session.add(materia); db.session.flush()
            else:
                materia.ordem = ordem
            for c_ordem, nome_conteudo in enumerate(conteudos, 1):
                c = Conteudo.query.filter_by(materia_id=materia.id, nome=nome_conteudo).first()
                if not c:
                    db.session.add(Conteudo(materia_id=materia.id, nome=nome_conteudo, ordem=c_ordem))
    db.session.commit()
    print(f'Edital carregado: {len(SD_EDITAL)} matérias SD + {len(CFO_EDITAL)} matérias CFO.')


@app.cli.command('create-admin')
def create_admin():
    username = os.environ.get('ADMIN_USERNAME', 'admin'); email = os.environ.get('ADMIN_EMAIL', 'admin@pmba.local'); telefone = os.environ.get('ADMIN_TELEFONE', '00000000000'); password = os.environ.get('ADMIN_PASSWORD', 'admin123')
    existing = User.query.filter((User.username == username) | (User.email == email)).first()
    if existing: existing.is_admin = True; db.session.commit(); print(f'Usuário {existing.username} agora é administrador.'); return
    user = User(username=username, email=email, telefone=telefone, password_hash=generate_password_hash(password), is_admin=True); db.session.add(user); db.session.commit(); print(f'Administrador criado: {username} / senha: {password}')

@app.cli.command('reset-db')
def reset_db():
    db.drop_all(); db.create_all(); print('Banco recriado do zero. Todas as tabelas estão vazias.')

@app.get('/health')
def health():
    # Lightweight endpoint for platform health checks.
    return jsonify({'status': 'ok'}), 200


with app.app_context():
    db.create_all()


if __name__ == '__main__':
    # Local development only. Production uses Gunicorn.
    port = int(os.environ.get('PORT', '5000'))
    debug = os.environ.get('FLASK_DEBUG', '0') == '1'
    app.run(host='0.0.0.0', port=port, debug=debug)
