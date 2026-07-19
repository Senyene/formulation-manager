from flask import Flask, render_template, redirect, url_for, request, flash
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
import os

# Initialize the app
app = Flask(__name__)
app.config['SECRET_KEY'] = 'dev-secret-key-change-later'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///formulation.db'
db = SQLAlchemy(app)

# Login manager setup
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# Database Models
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # qc, rd, planner, production, factory, audit, md
    display_name = db.Column(db.String(100), nullable=False)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Routes
@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        
        if user and check_password_hash(user.password_hash, password):
            login_user(user)
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid username or password')
    return render_template('login.html')

@app.route('/dashboard')
@login_required
def dashboard():
    # Route to role-specific dashboard
    role_templates = {
        'qc': 'dashboard_qc.html',
        'rd': 'dashboard_rd.html',
        'planner': 'dashboard_planner.html',
        'production': 'dashboard_production.html',
        'factory': 'dashboard_factory.html',
        'audit': 'dashboard_audit.html',
        'md': 'dashboard_md.html'
    }
    template = role_templates.get(current_user.role, 'login.html')
    return render_template(template)

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

# Create database and seed users
def init_db():
    with app.app_context():
        db.create_all()
        
        # Only seed if no users exist
        if User.query.count() == 0:
            users = [
                User(username='qc_user', password_hash=generate_password_hash('pass123'), role='qc', display_name='QC Operator'),
                User(username='rd_user', password_hash=generate_password_hash('pass123'), role='rd', display_name='R&D Scientist'),
                User(username='planner', password_hash=generate_password_hash('pass123'), role='planner', display_name='Production Planner'),
                User(username='prod_mgr', password_hash=generate_password_hash('pass123'), role='production', display_name='Production Manager'),
                User(username='factory_mgr', password_hash=generate_password_hash('pass123'), role='factory', display_name='Factory Manager'),
                User(username='auditor', password_hash=generate_password_hash('pass123'), role='audit', display_name='Auditor'),
                User(username='md_user', password_hash=generate_password_hash('pass123'), role='md', display_name='Managing Director'),
            ]
            db.session.add_all(users)
            db.session.commit()
            print("✅ Database created and seeded with test users!")

if __name__ == '__main__':
    init_db()
    app.run(debug=True, host='0.0.0.0', port=5000)