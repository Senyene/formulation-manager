from flask import Flask, render_template, redirect, url_for, request, flash, session, get_flashed_messages, Response
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
from functools import wraps
import json
import csv
import io
import os
import secrets

# Initialize the app
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', secrets.token_hex(32))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///formulation.db'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=30)
db = SQLAlchemy(app)

# Login manager setup
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Please log in to access this page.'
login_manager.login_message_category = 'warning'

# ============================================
# DATABASE MODELS
# ============================================

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    display_name = db.Column(db.String(100), nullable=False)
    last_active = db.Column(db.DateTime, default=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)

class RawMaterial(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(20), unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    supplier = db.Column(db.String(100))
    unit = db.Column(db.String(20), default='kg')
    cost_per_unit = db.Column(db.Float)
    stock_level = db.Column(db.Float, default=0)
    created_by = db.Column(db.String(80))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Formula(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(20), unique=True, nullable=False)
    name = db.Column(db.String(200), nullable=False)
    version = db.Column(db.String(10), default='1.0')
    status = db.Column(db.String(20), default='draft')
    created_by = db.Column(db.String(80))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    approved_by = db.Column(db.String(80), nullable=True)
    approved_at = db.Column(db.DateTime, nullable=True)
    batch_size = db.Column(db.Float, default=0)
    ingredients = db.relationship('FormulaIngredient', backref='formula', lazy=True)

class FormulaIngredient(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    formula_id = db.Column(db.Integer, db.ForeignKey('formula.id'), nullable=False)
    raw_material_id = db.Column(db.Integer, db.ForeignKey('raw_material.id'), nullable=False)
    quantity = db.Column(db.Float, nullable=False)
    unit = db.Column(db.String(20), default='kg')
    material = db.relationship('RawMaterial')

class QCTestResult(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    formula_id = db.Column(db.Integer, db.ForeignKey('formula.id'), nullable=False)
    batch_number = db.Column(db.String(50), nullable=False)
    test_date = db.Column(db.DateTime, default=datetime.utcnow)
    tested_by = db.Column(db.String(80))
    parameters = db.Column(db.Text)
    status = db.Column(db.String(20), default='pending')
    notes = db.Column(db.Text)
    formula = db.relationship('Formula')

class ProductionBatch(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    formula_id = db.Column(db.Integer, db.ForeignKey('formula.id'), nullable=False)
    batch_number = db.Column(db.String(50), nullable=False)
    planned_date = db.Column(db.DateTime)
    actual_date = db.Column(db.DateTime)
    quantity_planned = db.Column(db.Float, default=0)
    quantity_produced = db.Column(db.Float, default=0)
    status = db.Column(db.String(20), default='planned')
    notes = db.Column(db.Text)
    created_by = db.Column(db.String(80))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    formula = db.relationship('Formula')

class QCParameter(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    unit = db.Column(db.String(20), default='')
    spec_min = db.Column(db.Float, nullable=True)
    spec_max = db.Column(db.Float, nullable=True)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user = db.Column(db.String(80))
    role = db.Column(db.String(20))
    action = db.Column(db.String(200))
    details = db.Column(db.Text)
    ip_address = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class HourlyWeightCheck(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    check_time = db.Column(db.DateTime, default=datetime.utcnow)
    product = db.Column(db.String(100), nullable=False)
    weight = db.Column(db.Float, nullable=False)
    unit = db.Column(db.String(10), default='g')
    spec_min = db.Column(db.Float, nullable=True)
    spec_max = db.Column(db.Float, nullable=True)
    status = db.Column(db.String(20), default='pass')
    recorded_by = db.Column(db.String(80))
    shift = db.Column(db.String(1))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class FinishedGoodsTest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    formula_id = db.Column(db.Integer, db.ForeignKey('formula.id'), nullable=False)
    batch_number = db.Column(db.String(50), nullable=False)
    sku = db.Column(db.String(20), nullable=False)  # Tin: 2200g, 800g, etc; Pouch: 1100g, etc; Sachet: 70g
    package_type = db.Column(db.String(20), nullable=False)  # Tin, Pouch, Sachet
    test_date = db.Column(db.DateTime, default=datetime.utcnow)
    tested_by = db.Column(db.String(80))
    parameters = db.Column(db.Text)  # JSON same as in-process
    pasteurizer_temp = db.Column(db.Float, nullable=True)
    status = db.Column(db.String(20), default='pending')
    notes = db.Column(db.Text)
    formula = db.relationship('Formula')

class EndOfShiftReport(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    shift = db.Column(db.String(1), nullable=False)
    report_date = db.Column(db.Date, nullable=False)
    section = db.Column(db.String(30), nullable=False)  # Tin, Sachet_Pouch, Cube, Dry
    mixing_plant = db.Column(db.String(100))
    product = db.Column(db.String(200))
    filling_sku = db.Column(db.String(50))
    packaging_sku = db.Column(db.String(50))
    total_pallets = db.Column(db.Integer, default=0)
    total_cartons = db.Column(db.Integer, default=0)
    notes = db.Column(db.Text)
    submitted_by = db.Column(db.String(80))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# ============================================
# HELPER: Audit logging
# ============================================
def log_action(action, details=''):
    if current_user.is_authenticated:
        log = AuditLog(
            user=current_user.display_name,
            role=current_user.role,
            action=action,
            details=str(details)[:500],
            ip_address=request.remote_addr
        )
        db.session.add(log)
        db.session.commit()

# ============================================
# HELPER: Session timeout check
# ============================================
@app.before_request
def check_session_timeout():
    if current_user.is_authenticated:
        if current_user.last_active:
            idle_time = datetime.utcnow() - current_user.last_active
            if idle_time > app.config['PERMANENT_SESSION_LIFETIME']:
                logout_user()
                flash('Session expired due to inactivity. Please log in again.', 'warning')
                return redirect(url_for('login'))
        current_user.last_active = datetime.utcnow()
        db.session.commit()

# ============================================
# HELPER: Input validation
# ============================================
def safe_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default

def safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default

# ============================================
# HELPER: Formula details access
# ============================================
def can_view_formula_details():
    return current_user.role in ['rd', 'md']

# ============================================
# ERROR HANDLERS
# ============================================
@app.errorhandler(404)
def not_found(e):
    return render_template('error.html', code=404, message='Page not found'), 404

@app.errorhandler(500)
def server_error(e):
    db.session.rollback()
    return render_template('error.html', code=500, message='Internal server error'), 500

# ============================================
# ROUTES
# ============================================

@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        
        if not username or not password:
            flash('Please enter both username and password.', 'error')
            return render_template('login.html')
        
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password_hash, password):
            if not user.is_active:
                flash('This account has been deactivated.', 'error')
                return render_template('login.html')
            login_user(user)
            user.last_active = datetime.utcnow()
            db.session.commit()
            log_action('LOGIN')
            flash(f'Welcome back, {user.display_name}!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid username or password.', 'error')
    return render_template('login.html')

@app.route('/dashboard')
@login_required
def dashboard():
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
    show_formula_details = can_view_formula_details()
    
    if current_user.role == 'qc':
        # Check if shift report submitted
        now = datetime.utcnow()
        shift = 'A' if 7 <= now.hour < 19 else 'B'
        today = now.date()
        report_exists = EndOfShiftReport.query.filter_by(shift=shift, report_date=today).first()
        
        # Pass shift report status
        shift_report_done = report_exists is not None

        formulas = Formula.query.filter_by(status='approved').all()
        recent_tests = QCTestResult.query.order_by(QCTestResult.test_date.desc()).limit(20).all()
        qc_parameters = QCParameter.query.filter_by(is_active=True).order_by(QCParameter.name).all()
        tests_json = [{'id': t.id, 'batch': t.batch_number, 'formula_id': t.formula_id, 'formula_code': t.formula.code if t.formula else 'N/A', 'date': t.test_date.strftime('%Y-%m-%d') if t.test_date else '', 'status': t.status, 'parameters': json.loads(t.parameters) if t.parameters else []} for t in recent_tests]
        return render_template(template, formulas=formulas, recent_tests=recent_tests, qc_parameters=qc_parameters, recent_tests_json=json.dumps(tests_json), show_formula_details=False)
    
    elif current_user.role == 'rd':
        formulas = Formula.query.order_by(Formula.created_at.desc()).all()
        materials = RawMaterial.query.order_by(RawMaterial.name).all()
        qc_feed = QCTestResult.query.order_by(QCTestResult.test_date.desc()).limit(20).all()
        qc_parameters = QCParameter.query.filter_by(is_active=True).order_by(QCParameter.name).all()
        all_tests = QCTestResult.query.order_by(QCTestResult.test_date.asc()).all()
        tests_json = [{'id': t.id, 'batch': t.batch_number, 'formula_id': t.formula_id, 'formula_code': t.formula.code if t.formula else 'N/A', 'date': t.test_date.strftime('%Y-%m-%d') if t.test_date else '', 'status': t.status, 'parameters': json.loads(t.parameters) if t.parameters else []} for t in all_tests]
        params_json = [{'name': p.name, 'unit': p.unit or '', 'spec_min': p.spec_min, 'spec_max': p.spec_max} for p in qc_parameters]
        return render_template(template, formulas=formulas, materials=materials, qc_feed=qc_feed, qc_parameters_json=json.dumps(params_json), all_tests_json=json.dumps(tests_json), show_formula_details=True)
    
    elif current_user.role == 'planner':
        consumption_data = {}
        qc_batches = QCTestResult.query.order_by(QCTestResult.test_date.desc()).all()
        for batch in qc_batches:
            if batch.formula and batch.formula.ingredients:
                for ingredient in batch.formula.ingredients:
                    material = ingredient.material
                    if material:
                        key = f"{material.code}-{material.name}"
                        if key not in consumption_data:
                            consumption_data[key] = {'material_code': material.code, 'material_name': material.name, 'unit': ingredient.unit, 'total_used': 0, 'batch_count': 0, 'batches': []}
                        consumption_data[key]['total_used'] += ingredient.quantity
                        consumption_data[key]['batch_count'] += 1
                        consumption_data[key]['batches'].append({'batch_number': batch.batch_number, 'formula_code': batch.formula.code, 'quantity': ingredient.quantity, 'date': batch.test_date.strftime('%d-%b-%Y') if batch.test_date else ''})
        production_batches = ProductionBatch.query.order_by(ProductionBatch.planned_date.desc()).all()
        approved_formulas = Formula.query.filter_by(status='approved').all()
        return render_template(template, qc_batches=qc_batches, consumption_data=consumption_data, production_batches=production_batches, approved_formulas=approved_formulas, show_formula_details=False)
    
    elif current_user.role == 'production':
        qc_feed = QCTestResult.query.order_by(QCTestResult.test_date.desc()).limit(15).all()
        production_batches = ProductionBatch.query.order_by(ProductionBatch.planned_date.desc()).all()
        total_produced = sum(b.quantity_produced or 0 for b in production_batches if b.status == 'completed')
        total_planned = sum(b.quantity_planned or 0 for b in production_batches)
        batches_today = len([b for b in production_batches if b.planned_date and b.planned_date.date() == datetime.utcnow().date()])
        qc_pass_rate = round((len([t for t in qc_feed if t.status == 'pass']) / len(qc_feed)) * 100) if qc_feed else 0
        return render_template(template, qc_feed=qc_feed, production_batches=production_batches, total_produced=total_produced, total_planned=total_planned, batches_today=batches_today, qc_pass_rate=qc_pass_rate, show_formula_details=False)
    
    elif current_user.role == 'factory':
        production_batches = ProductionBatch.query.order_by(ProductionBatch.planned_date.desc()).limit(20).all()
        qc_summary = QCTestResult.query.order_by(QCTestResult.test_date.desc()).limit(20).all()
        total_qc_tests = len(qc_summary)
        passed_qc = len([t for t in qc_summary if t.status == 'pass'])
        failed_qc = len([t for t in qc_summary if t.status == 'fail'])
        completed_batches = [b for b in production_batches if b.status == 'completed']
        efficiency = round(sum([(b.quantity_produced or 0) / b.quantity_planned * 100 for b in completed_batches if b.quantity_planned and b.quantity_planned > 0]) / len([b for b in completed_batches if b.quantity_planned and b.quantity_planned > 0])) if [b for b in completed_batches if b.quantity_planned and b.quantity_planned > 0] else 0
        low_stock_materials = RawMaterial.query.filter(RawMaterial.stock_level < 100).all()
        return render_template(template, production_batches=production_batches, qc_summary=qc_summary, total_qc_tests=total_qc_tests, passed_qc=passed_qc, failed_qc=failed_qc, efficiency=efficiency, low_stock_materials=low_stock_materials, show_formula_details=False)
    
    elif current_user.role == 'audit':
        qc_audit = QCTestResult.query.order_by(QCTestResult.test_date.desc()).all()
        production_audit = ProductionBatch.query.order_by(ProductionBatch.planned_date.desc()).all()
        formulas = Formula.query.all()
        total_tests = len(qc_audit)
        passed_tests = len([t for t in qc_audit if t.status == 'pass'])
        failed_tests = len([t for t in qc_audit if t.status == 'fail'])
        pending_tests = len([t for t in qc_audit if t.status == 'pending'])
        testers = {}
        for test in qc_audit:
            if test.tested_by:
                testers[test.tested_by] = testers.get(test.tested_by, 0) + 1
        return render_template(template, qc_audit=qc_audit, production_audit=production_audit, formulas=formulas, total_tests=total_tests, passed_tests=passed_tests, failed_tests=failed_tests, pending_tests=pending_tests, testers=testers, show_formula_details=False)
    
    elif current_user.role == 'md':
        total_formulas = Formula.query.count()
        approved_formulas = Formula.query.filter_by(status='approved').count()
        pending_approvals = Formula.query.filter_by(status='pending_approval').count()
        total_materials = RawMaterial.query.count()
        all_qc = QCTestResult.query.all()
        total_qc_tests = len(all_qc)
        qc_pass_rate = round((len([t for t in all_qc if t.status == 'pass']) / total_qc_tests * 100)) if total_qc_tests > 0 else 0
        thirty_days_ago = datetime.utcnow().replace(day=1)
        monthly_qc = [t for t in all_qc if t.test_date and t.test_date >= thirty_days_ago]
        monthly_pass = len([t for t in monthly_qc if t.status == 'pass'])
        monthly_fail = len([t for t in monthly_qc if t.status == 'fail'])
        all_production = ProductionBatch.query.all()
        total_batches = len(all_production)
        completed_batches = len([b for b in all_production if b.status == 'completed'])
        total_produced = sum(b.quantity_produced or 0 for b in all_production)
        total_planned = sum(b.quantity_planned or 0 for b in all_production)
        production_efficiency = round((total_produced / total_planned * 100)) if total_planned > 0 else 0
        materials = RawMaterial.query.all()
        total_inventory_value = sum((m.stock_level or 0) * (m.cost_per_unit or 0) for m in materials)
        low_stock_count = len([m for m in materials if m.stock_level and m.stock_level < 50])
        recent_qc = QCTestResult.query.order_by(QCTestResult.test_date.desc()).limit(5).all()
        recent_production = ProductionBatch.query.order_by(ProductionBatch.created_at.desc()).limit(5).all()
        pending_formulas = Formula.query.filter_by(status='pending_approval').order_by(Formula.created_at.desc()).all()
        dept_activity = {'R&D': Formula.query.count(), 'QC': total_qc_tests, 'Planning': total_batches, 'Production': completed_batches}
        return render_template(template, total_formulas=total_formulas, approved_formulas=approved_formulas, pending_approvals=pending_approvals, total_materials=total_materials, total_qc_tests=total_qc_tests, qc_pass_rate=qc_pass_rate, monthly_qc_count=len(monthly_qc), monthly_pass=monthly_pass, monthly_fail=monthly_fail, total_batches=total_batches, completed_batches=completed_batches, total_produced=total_produced, total_planned=total_planned, production_efficiency=production_efficiency, total_inventory_value=total_inventory_value, low_stock_count=low_stock_count, recent_qc=recent_qc, recent_production=recent_production, dept_activity=dept_activity, failed_tests=len([t for t in all_qc if t.status == 'fail']), pending_formulas=pending_formulas, show_formula_details=True)
    
    return render_template(template, show_formula_details=False, shift_report_done=shift_report_done)

# ============================================
# QC ROUTE
# ============================================

@app.route('/qc/submit', methods=['POST'])
@login_required
def submit_qc_result():
    if current_user.role != 'qc':
        flash('Access denied.', 'error')
        return redirect(url_for('dashboard'))
    
    formula_id = safe_int(request.form.get('formula_id'))
    test_date = request.form.get('test_date')
    notes = request.form.get('notes', '').strip()[:500]
    
    if not formula_id:
        flash('Please select a formula.', 'error')
        return redirect(url_for('dashboard'))
    
    param_names = request.form.getlist('param_name[]')
    param_results = request.form.getlist('param_result[]')
    param_units = request.form.getlist('param_unit[]')
    param_specs = request.form.getlist('param_spec[]')
    
    parameters = []
    all_pass = True
    for i in range(len(param_names)):
        if param_names[i] and param_names[i].strip():
            spec_range = param_specs[i] if i < len(param_specs) else ''
            result_val = param_results[i] if i < len(param_results) else ''
            param_pass = True
            if spec_range and result_val:
                try:
                    parts = spec_range.replace('≥', '').replace('≤', '').strip().split('-')
                    if len(parts) == 2:
                        result_float = float(result_val)
                        param_pass = float(parts[0].strip()) <= result_float <= float(parts[1].strip())
                    elif '≥' in spec_range:
                        result_float = float(result_val)
                        param_pass = result_float >= float(parts[0].strip())
                    elif '≤' in spec_range:
                        result_float = float(result_val)
                        param_pass = result_float <= float(parts[0].strip())
                except:
                    param_pass = True
            if not param_pass:
                all_pass = False
            parameters.append({'name': param_names[i].strip(), 'result': result_val, 'unit': param_units[i] if i < len(param_units) else '', 'spec': spec_range, 'pass': param_pass})
    
    if not parameters:
        flash('Please add at least one test parameter.', 'error')
        return redirect(url_for('dashboard'))
    
    formula = Formula.query.get(formula_id) if formula_id else None
    batch_number = _generate_batch_number(formula)
    
    parsed_date = datetime.utcnow()
    if test_date:
        try:
            parsed_date = datetime.strptime(test_date, '%Y-%m-%dT%H:%M')
        except ValueError:
            pass
    
    test_result = QCTestResult(
        formula_id=formula_id,
        batch_number=batch_number,
        test_date=parsed_date,
        tested_by=current_user.display_name,
        parameters=json.dumps(parameters),
        status='pass' if all_pass else 'fail',
        notes=notes
    )
    
    db.session.add(test_result)
    db.session.commit()
    
    log_action('QC_SUBMIT', f'Batch: {batch_number}, Formula: {formula.code if formula else "N/A"}, Status: {test_result.status}')
    flash(f'Test result submitted! Batch: {batch_number}', 'success')
    return redirect(url_for('dashboard'))

# ============================================
# R&D ROUTES
# ============================================

@app.route('/rd/create-formula', methods=['POST'])
@login_required
def create_formula():
    if current_user.role != 'rd':
        flash('Access denied.', 'error')
        return redirect(url_for('dashboard'))
    code = request.form.get('code', '').strip()
    name = request.form.get('name', '').strip()
    version = request.form.get('version', '1.0').strip()
    
    if not code or not name:
        flash('Formula code and name are required.', 'error')
        return redirect(url_for('dashboard'))
    if len(code) > 20 or len(name) > 200:
        flash('Formula code or name is too long.', 'error')
        return redirect(url_for('dashboard'))
    if Formula.query.filter_by(code=code).first():
        flash('Formula code already exists!', 'error')
        return redirect(url_for('dashboard'))
    
    formula = Formula(code=code, name=name, version=version, status='draft', created_by=current_user.display_name)
    db.session.add(formula)
    db.session.commit()
    log_action('FORMULA_CREATE', f'Code: {code}, Name: {name}')
    flash(f'Formula {code} created successfully!', 'success')
    return redirect(url_for('dashboard'))

@app.route('/rd/add-ingredient', methods=['POST'])
@login_required
def add_ingredient():
    if current_user.role != 'rd':
        flash('Access denied.', 'error')
        return redirect(url_for('dashboard'))
    formula_id = safe_int(request.form.get('formula_id'))
    material_id = safe_int(request.form.get('material_id'))
    quantity = safe_float(request.form.get('quantity'))
    unit = request.form.get('unit', 'kg').strip()[:20]
    
    if not formula_id or not material_id or quantity <= 0:
        flash('Please fill all ingredient fields correctly.', 'error')
        return redirect(url_for('dashboard'))
    if FormulaIngredient.query.filter_by(formula_id=formula_id, raw_material_id=material_id).first():
        flash('This material is already in the formula.', 'error')
        return redirect(url_for('dashboard'))
    
    ingredient = FormulaIngredient(formula_id=formula_id, raw_material_id=material_id, quantity=quantity, unit=unit)
    db.session.add(ingredient)
    db.session.commit()
    _update_batch_size(formula_id)
    flash('Ingredient added!', 'success')
    return redirect(url_for('dashboard'))

@app.route('/rd/update-ingredient/<int:ingredient_id>', methods=['POST'])
@login_required
def update_ingredient(ingredient_id):
    if current_user.role != 'rd':
        flash('Access denied.', 'error')
        return redirect(url_for('dashboard'))
    ingredient = FormulaIngredient.query.get_or_404(ingredient_id)
    quantity = safe_float(request.form.get('quantity'))
    unit = request.form.get('unit', 'kg').strip()[:20]
    if quantity <= 0:
        flash('Quantity must be greater than zero.', 'error')
        return redirect(url_for('dashboard'))
    ingredient.quantity = quantity
    ingredient.unit = unit
    db.session.commit()
    _update_batch_size(ingredient.formula_id)
    flash('Ingredient updated!', 'success')
    return redirect(url_for('dashboard'))

@app.route('/rd/remove-ingredient/<int:ingredient_id>', methods=['POST'])
@login_required
def remove_ingredient(ingredient_id):
    if current_user.role != 'rd':
        flash('Access denied.', 'error')
        return redirect(url_for('dashboard'))
    ingredient = FormulaIngredient.query.get_or_404(ingredient_id)
    fid = ingredient.formula_id
    db.session.delete(ingredient)
    db.session.commit()
    _update_batch_size(fid)
    flash('Ingredient removed.', 'success')
    return redirect(url_for('dashboard'))

@app.route('/rd/submit-for-approval/<int:formula_id>', methods=['POST'])
@login_required
def submit_for_approval(formula_id):
    if current_user.role != 'rd':
        flash('Access denied.', 'error')
        return redirect(url_for('dashboard'))
    formula = Formula.query.get_or_404(formula_id)
    if formula.status == 'draft' and formula.ingredients:
        formula.status = 'pending_approval'
        db.session.commit()
        log_action('FORMULA_SUBMIT', f'Code: {formula.code}')
        flash(f'Formula {formula.code} submitted for MD approval!', 'success')
    else:
        flash('Formula must be in draft status and have ingredients.', 'error')
    return redirect(url_for('dashboard'))

@app.route('/rd/update-formula-status/<int:formula_id>', methods=['POST'])
@login_required
def update_formula_status(formula_id):
    if current_user.role != 'rd':
        flash('Access denied.', 'error')
        return redirect(url_for('dashboard'))
    formula = Formula.query.get_or_404(formula_id)
    new_status = request.form.get('status')
    if new_status in ['draft', 'archived']:
        formula.status = new_status
        db.session.commit()
        log_action('FORMULA_STATUS', f'Code: {formula.code}, Status: {new_status}')
        flash(f'Formula {formula.code} is now {new_status.upper()}.', 'success')
    return redirect(url_for('dashboard'))

@app.route('/rd/create-material', methods=['POST'])
@login_required
def create_material():
    if current_user.role != 'rd':
        flash('Access denied.', 'error')
        return redirect(url_for('dashboard'))
    code = request.form.get('code', '').strip()
    if not code:
        flash('Material code is required.', 'error')
        return redirect(url_for('dashboard'))
    if RawMaterial.query.filter_by(code=code).first():
        flash('Material code already exists!', 'error')
        return redirect(url_for('dashboard'))
    material = RawMaterial(
        code=code,
        name=request.form.get('name', '').strip(),
        supplier=request.form.get('supplier', '').strip()[:100],
        unit=request.form.get('unit', 'kg').strip()[:20],
        cost_per_unit=safe_float(request.form.get('cost_per_unit', 0)),
        stock_level=safe_float(request.form.get('stock_level', 0)),
        created_by=current_user.display_name
    )
    db.session.add(material)
    db.session.commit()
    flash(f'Material {code} created!', 'success')
    return redirect(url_for('dashboard'))

@app.route('/rd/update-material/<int:material_id>', methods=['POST'])
@login_required
def update_material(material_id):
    if current_user.role != 'rd':
        flash('Access denied.', 'error')
        return redirect(url_for('dashboard'))
    material = RawMaterial.query.get_or_404(material_id)
    material.name = request.form.get('name', material.name).strip()
    material.supplier = request.form.get('supplier', material.supplier).strip()[:100]
    material.unit = request.form.get('unit', material.unit).strip()[:20]
    material.cost_per_unit = safe_float(request.form.get('cost_per_unit', material.cost_per_unit))
    material.stock_level = safe_float(request.form.get('stock_level', material.stock_level))
    db.session.commit()
    flash(f'Material {material.code} updated!', 'success')
    return redirect(url_for('dashboard'))

# ============================================
# MD APPROVAL ROUTES
# ============================================

@app.route('/md/approve/<int:formula_id>', methods=['POST'])
@login_required
def approve_formula(formula_id):
    if current_user.role != 'md':
        flash('Access denied.', 'error')
        return redirect(url_for('dashboard'))
    formula = Formula.query.get_or_404(formula_id)
    if formula.status == 'pending_approval':
        formula.status = 'approved'
        formula.approved_by = current_user.display_name
        formula.approved_at = datetime.utcnow()
        db.session.commit()
        log_action('FORMULA_APPROVE', f'Code: {formula.code}')
        flash(f'Formula {formula.code} has been APPROVED!', 'success')
    return redirect(url_for('dashboard'))

@app.route('/md/reject/<int:formula_id>', methods=['POST'])
@login_required
def reject_formula(formula_id):
    if current_user.role != 'md':
        flash('Access denied.', 'error')
        return redirect(url_for('dashboard'))
    formula = Formula.query.get_or_404(formula_id)
    if formula.status == 'pending_approval':
        formula.status = 'draft'
        db.session.commit()
        log_action('FORMULA_REJECT', f'Code: {formula.code}')
        flash(f'Formula {formula.code} has been REJECTED.', 'error')
    return redirect(url_for('dashboard'))

# ============================================
# QC PARAMETER MANAGEMENT
# ============================================

@app.route('/rd/parameters')
@login_required
def manage_parameters():
    if current_user.role not in ['rd', 'qc']:
        flash('Access denied.', 'error')
        return redirect(url_for('dashboard'))
    parameters = QCParameter.query.order_by(QCParameter.name).all()
    return render_template('parameters.html', parameters=parameters)

@app.route('/rd/parameters/add', methods=['POST'])
@login_required
def add_parameter():
    if current_user.role != 'rd':
        flash('Access denied.', 'error')
        return redirect(url_for('dashboard'))
    name = request.form.get('name', '').strip()
    if not name:
        flash('Parameter name is required.', 'error')
        return redirect(url_for('manage_parameters'))
    if QCParameter.query.filter_by(name=name).first():
        flash(f'Parameter "{name}" already exists!', 'error')
        return redirect(url_for('manage_parameters'))
    param = QCParameter(name=name, unit=request.form.get('unit', '').strip(), spec_min=safe_float(request.form.get('spec_min')) if request.form.get('spec_min') else None, spec_max=safe_float(request.form.get('spec_max')) if request.form.get('spec_max') else None, is_active=True)
    db.session.add(param)
    db.session.commit()
    flash(f'Parameter "{name}" added!', 'success')
    return redirect(url_for('manage_parameters'))

@app.route('/rd/parameters/toggle/<int:param_id>', methods=['POST'])
@login_required
def toggle_parameter(param_id):
    if current_user.role != 'rd':
        flash('Access denied.', 'error')
        return redirect(url_for('dashboard'))
    param = QCParameter.query.get_or_404(param_id)
    param.is_active = not param.is_active
    db.session.commit()
    flash(f'Parameter "{param.name}" {"activated" if param.is_active else "deactivated"}!', 'success')
    return redirect(url_for('manage_parameters'))

# ============================================
# PLANNER ROUTES
# ============================================

@app.route('/planner/create-batch', methods=['POST'])
@login_required
def create_production_batch():
    if current_user.role != 'planner':
        flash('Access denied.', 'error')
        return redirect(url_for('dashboard'))
    formula_id = safe_int(request.form.get('formula_id'))
    batch_number = request.form.get('batch_number', '').strip()
    planned_date = request.form.get('planned_date')
    quantity_planned = safe_float(request.form.get('quantity_planned'))
    notes = request.form.get('notes', '').strip()[:500]
    
    if not formula_id or not batch_number:
        flash('Formula and batch number are required.', 'error')
        return redirect(url_for('dashboard'))
    if ProductionBatch.query.filter_by(batch_number=batch_number).first():
        flash('Batch number already exists!', 'error')
        return redirect(url_for('dashboard'))
    
    batch = ProductionBatch(formula_id=formula_id, batch_number=batch_number, planned_date=datetime.strptime(planned_date, '%Y-%m-%d') if planned_date else datetime.utcnow(), quantity_planned=quantity_planned, status='planned', notes=notes, created_by=current_user.display_name)
    db.session.add(batch)
    db.session.commit()
    log_action('BATCH_CREATE', f'Batch: {batch_number}')
    flash(f'Production batch {batch_number} planned!', 'success')
    return redirect(url_for('dashboard'))

@app.route('/planner/update-batch/<int:batch_id>', methods=['POST'])
@login_required
def update_production_batch(batch_id):
    if current_user.role != 'planner':
        flash('Access denied.', 'error')
        return redirect(url_for('dashboard'))
    batch = ProductionBatch.query.get_or_404(batch_id)
    batch.quantity_produced = safe_float(request.form.get('quantity_produced', 0))
    new_status = request.form.get('status')
    if new_status in ['planned', 'in_progress', 'completed', 'cancelled']:
        batch.status = new_status
        if new_status == 'completed':
            batch.actual_date = datetime.utcnow()
    db.session.commit()
    flash(f'Batch {batch.batch_number} updated!', 'success')
    return redirect(url_for('dashboard'))

@app.route('/planner/export-consumption')
@login_required
def export_consumption():
    if current_user.role != 'planner':
        flash('Access denied.', 'error')
        return redirect(url_for('dashboard'))
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Material Code', 'Material Name', 'Total Used', 'Unit', 'Batch Count'])
    qc_batches = QCTestResult.query.all()
    consumption = {}
    for batch in qc_batches:
        if batch.formula and batch.formula.ingredients:
            for ing in batch.formula.ingredients:
                if ing.material:
                    key = ing.material.code
                    if key not in consumption:
                        consumption[key] = {'name': ing.material.name, 'total': 0, 'unit': ing.unit, 'count': 0}
                    consumption[key]['total'] += ing.quantity
                    consumption[key]['count'] += 1
    for code, data in consumption.items():
        writer.writerow([code, data['name'], data['total'], data['unit'], data['count']])
    output.seek(0)
    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-disposition": "attachment; filename=consumption_report.csv"})

# ============================================
# EXPORT ROUTES
# ============================================

@app.route('/export/qc-results')
@login_required
def export_qc_results():
    """Export QC results as CSV (Excel-compatible)."""
    if current_user.role not in ['qc', 'rd', 'md', 'audit']:
        flash('Access denied.', 'error')
        return redirect(url_for('dashboard'))
    
    output = io.StringIO()
    output.write('\ufeff')  # BOM for Excel UTF-8 compatibility
    writer = csv.writer(output)
    writer.writerow(['Batch Number', 'Formula Code', 'Formula Name', 'Test Date', 'Tester', 'Status', 'Parameters', 'Notes'])
    
    tests = QCTestResult.query.order_by(QCTestResult.test_date.desc()).all()
    for t in tests:
        params_str = ''
        if t.parameters:
            try:
                params = json.loads(t.parameters)
                params_str = '; '.join([f"{p.get('name','')}={p.get('result','')}{p.get('unit','')}" for p in params])
            except:
                params_str = t.parameters[:200]
        
        writer.writerow([
            t.batch_number,
            t.formula.code if t.formula else 'N/A',
            t.formula.name if t.formula else 'N/A',
            t.test_date.strftime('%Y-%m-%d %H:%M') if t.test_date else '',
            t.tested_by,
            t.status.upper(),
            params_str,
            t.notes or ''
        ])
    
    output.seek(0)
    log_action('EXPORT_QC', f'By: {current_user.display_name}')
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-disposition": "attachment; filename=qc_results_export.csv"}
    )

@app.route('/export/formulas')
@login_required
def export_formulas():
    """Export formulas list as CSV (Excel-compatible)."""
    if current_user.role not in ['rd', 'md', 'audit']:
        flash('Access denied.', 'error')
        return redirect(url_for('dashboard'))
    
    output = io.StringIO()
    output.write('\ufeff')
    writer = csv.writer(output)
    
    if can_view_formula_details():
        writer.writerow(['Code', 'Name', 'Version', 'Status', 'Batch Size (kg)', 'Created By', 'Created Date', 'Approved By', 'Ingredients'])
        formulas = Formula.query.order_by(Formula.code).all()
        for f in formulas:
            ings = '; '.join([f"{i.material.name if i.material else '?'}: {i.quantity}{i.unit}" for i in f.ingredients])
            writer.writerow([
                f.code, f.name, f.version, f.status.upper(), f.batch_size,
                f.created_by, f.created_at.strftime('%Y-%m-%d') if f.created_at else '',
                f.approved_by or '', ings
            ])
    else:
        writer.writerow(['Code', 'Name', 'Version', 'Status', 'Created Date'])
        formulas = Formula.query.order_by(Formula.code).all()
        for f in formulas:
            writer.writerow([f.code, f.name, f.version, f.status.upper(), f.created_at.strftime('%Y-%m-%d') if f.created_at else ''])
    
    output.seek(0)
    log_action('EXPORT_FORMULAS', f'By: {current_user.display_name}')
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-disposition": "attachment; filename=formulas_export.csv"}
    )

@app.route('/export/formula/<int:formula_id>')
@login_required
def export_single_formula(formula_id):
    """Export a single formula as CSV."""
    if current_user.role not in ['rd', 'md', 'audit']:
        flash('Access denied.', 'error')
        return redirect(url_for('dashboard'))
    
    formula = Formula.query.get_or_404(formula_id)
    
    output = io.StringIO()
    output.write('\ufeff')
    writer = csv.writer(output)
    
    if can_view_formula_details():
        writer.writerow(['Code', 'Name', 'Version', 'Status', 'Batch Size (kg)', 'Created By', 'Created Date', 'Approved By'])
        writer.writerow([formula.code, formula.name, formula.version, formula.status.upper(), formula.batch_size, formula.created_by, formula.created_at.strftime('%Y-%m-%d') if formula.created_at else '', formula.approved_by or ''])
        writer.writerow([])
        writer.writerow(['Ingredient Code', 'Ingredient Name', 'Quantity', 'Unit'])
        for ing in formula.ingredients:
            mat = ing.material
            writer.writerow([mat.code if mat else '?', mat.name if mat else 'Unknown', ing.quantity, ing.unit])
    else:
        writer.writerow(['Code', 'Name', 'Version', 'Status'])
        writer.writerow([formula.code, formula.name, formula.version, formula.status.upper()])
    
    output.seek(0)
    log_action('EXPORT_FORMULA', f'Code: {formula.code}')
    filename = f"formula_{formula.code}_{formula.name.replace(' ', '_')}.csv"
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-disposition": f"attachment; filename={filename}"}
    )

# ============================================
# HOURLY WEIGHT CHECK ROUTES
# ============================================

@app.route('/qc/weight-check', methods=['GET', 'POST'])
@login_required
def weight_check():
    if current_user.role != 'qc':
        flash('Access denied.', 'error')
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        product = request.form.get('product', '').strip()
        weight = safe_float(request.form.get('weight', 0))
        unit = request.form.get('unit', 'g')
        spec_min = safe_float(request.form.get('spec_min')) if request.form.get('spec_min') else None
        spec_max = safe_float(request.form.get('spec_max')) if request.form.get('spec_max') else None
        
        if not product or weight <= 0:
            flash('Product and weight are required.', 'error')
            return redirect(url_for('weight_check'))
        
        now = datetime.utcnow()
        shift = 'A' if 7 <= now.hour < 19 else 'B'
        status = 'pass'
        if spec_min and spec_max:
            status = 'pass' if spec_min <= weight <= spec_max else 'fail'
        elif spec_min:
            status = 'pass' if weight >= spec_min else 'fail'
        elif spec_max:
            status = 'pass' if weight <= spec_max else 'fail'
        
        check = HourlyWeightCheck(
            check_time=now,
            product=product,
            weight=weight,
            unit=unit,
            spec_min=spec_min,
            spec_max=spec_max,
            status=status,
            recorded_by=current_user.display_name,
            shift=shift
        )
        db.session.add(check)
        db.session.commit()
        log_action('WEIGHT_CHECK', f'Product: {product}, Weight: {weight}{unit}')
        flash('Weight check recorded!', 'success')
        return redirect(url_for('weight_check'))
    
    today = datetime.utcnow().date()
    checks = HourlyWeightCheck.query.filter(
        db.func.date(HourlyWeightCheck.check_time) == today
    ).order_by(HourlyWeightCheck.check_time.desc()).all()
    
    return render_template('weight_check.html', checks=checks)

# ============================================
# FINISHED GOODS ANALYSIS ROUTES
# ============================================

@app.route('/qc/finished-goods', methods=['GET', 'POST'])
@login_required
def finished_goods():
    if current_user.role != 'qc':
        flash('Access denied.', 'error')
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        formula_id = safe_int(request.form.get('formula_id'))
        sku = request.form.get('sku', '').strip()
        package_type = request.form.get('package_type', '').strip()
        pasteurizer_temp = safe_float(request.form.get('pasteurizer_temp'))
        notes = request.form.get('notes', '').strip()[:500]
        
        if not formula_id or not sku or not package_type:
            flash('Formula, SKU, and package type are required.', 'error')
            return redirect(url_for('finished_goods'))
        
        param_names = request.form.getlist('param_name[]')
        param_results = request.form.getlist('param_result[]')
        param_units = request.form.getlist('param_unit[]')
        param_specs = request.form.getlist('param_spec[]')
        
        parameters = []
        all_pass = True
        for i in range(len(param_names)):
            if param_names[i] and param_names[i].strip():
                spec_range = param_specs[i] if i < len(param_specs) else ''
                result_val = param_results[i] if i < len(param_results) else ''
                param_pass = True
                if spec_range and result_val:
                    try:
                        parts = spec_range.replace('≥','').replace('≤','').strip().split('-')
                        if len(parts) == 2:
                            param_pass = float(parts[0].strip()) <= float(result_val) <= float(parts[1].strip())
                    except:
                        param_pass = True
                if not param_pass:
                    all_pass = False
                parameters.append({'name': param_names[i].strip(), 'result': result_val, 'unit': param_units[i] if i < len(param_units) else '', 'spec': spec_range, 'pass': param_pass})
        
        formula = Formula.query.get(formula_id)
        batch_number = _generate_batch_number(formula)
        
        # Check pasteurizer temp
        pasteurizer_pass = 87 <= pasteurizer_temp <= 97 if pasteurizer_temp else True
        if not pasteurizer_pass:
            all_pass = False
        
        test = FinishedGoodsTest(
            formula_id=formula_id,
            batch_number=batch_number,
            sku=sku,
            package_type=package_type,
            test_date=datetime.utcnow(),
            tested_by=current_user.display_name,
            parameters=json.dumps(parameters),
            pasteurizer_temp=pasteurizer_temp if pasteurizer_temp else None,
            status='pass' if all_pass else 'fail',
            notes=notes
        )
        db.session.add(test)
        db.session.commit()
        log_action('FINISHED_GOODS', f'Batch: {batch_number}, SKU: {sku}, Status: {test.status}')
        flash(f'Finished goods test submitted! Batch: {batch_number}', 'success')
        return redirect(url_for('finished_goods'))
    
    formulas = Formula.query.filter_by(status='approved').all()
    qc_parameters = QCParameter.query.filter_by(is_active=True).order_by(QCParameter.name).all()
    recent_tests = FinishedGoodsTest.query.order_by(FinishedGoodsTest.test_date.desc()).limit(20).all()
    now = datetime.utcnow()
    shift = 'A' if 7 <= now.hour < 19 else 'B'
    return render_template('finished_goods.html', formulas=formulas, qc_parameters=qc_parameters, recent_tests=recent_tests, shift=shift, today=now.date())

# ============================================
# END OF SHIFT REPORT ROUTES
# ============================================

@app.route('/qc/shift-report', methods=['GET', 'POST'])
@login_required
def shift_report():
    if current_user.role != 'qc':
        flash('Access denied.', 'error')
        return redirect(url_for('dashboard'))
    
    now = datetime.utcnow()
    shift = 'A' if 7 <= now.hour < 19 else 'B'
    today = now.date()
    
    if request.method == 'POST':
        all_notes = request.form.get('notes', '').strip()[:1000]
        
        # Lab section - combine sample source data
        lab_formulas = request.form.getlist('lab_sample_formula[]')
        lab_sources = request.form.getlist('lab_sample_source[]')
        lab_counts = request.form.getlist('lab_sample_count[]')
        
        lab_sample_parts = []
        for i in range(len(lab_formulas)):
            if lab_formulas[i]:
                lab_sample_parts.append(
                    f"{lab_formulas[i]} ({lab_sources[i] if i < len(lab_sources) else '?'})"
                    f" x{safe_int(lab_counts[i]) if i < len(lab_counts) else 0}"
                )
        
        lab_report = EndOfShiftReport(
            shift=shift,
            report_date=today,
            section='Laboratory',
            mixing_plant=', '.join(lab_sample_parts) if lab_sample_parts else 'None',
            product='Calibration: pH=' + request.form.get('lab_ph_calibrated', '?') + 
                    ' Ref=' + request.form.get('lab_refracto_calibrated', '?') + 
                    ' Therm=' + request.form.get('lab_thermo_calibrated', '?') + 
                    ' Scale=' + request.form.get('lab_scale_calibrated', '?'),
            filling_sku='Concentrate: ' + request.form.get('lab_concentrate_done', '?'),
            packaging_sku='',
            total_pallets=0,
            total_cartons=0,
            notes=request.form.get('lab_concentrate_notes', ''),
            submitted_by=current_user.display_name
        )
        db.session.add(lab_report)
        
        # Helper to save section rows
        def save_section_rows(section_name, formula_field, filling_field, packaging_field, pallets_field, cartons_field):
            formulas = request.form.getlist(formula_field)
            fillings = request.form.getlist(filling_field)
            packagings = request.form.getlist(packaging_field)
            pallets = request.form.getlist(pallets_field)
            cartons = request.form.getlist(cartons_field)
            
            for i in range(len(formulas)):
                if formulas[i]:
                    report = EndOfShiftReport(
                        shift=shift,
                        report_date=today,
                        section=section_name,
                        mixing_plant='',
                        product=formulas[i],
                        filling_sku=fillings[i] if i < len(fillings) else '',
                        packaging_sku=packagings[i] if i < len(packagings) else '',
                        total_pallets=safe_int(pallets[i]) if i < len(pallets) else 0,
                        total_cartons=safe_int(cartons[i]) if i < len(cartons) else 0,
                        notes=all_notes,
                        submitted_by=current_user.display_name
                    )
                    db.session.add(report)
        
        # Dry section
        save_section_rows('Dry', 'dry_formula[]', 'dry_filling_sku[]', 'dry_packaging_sku[]', 'dry_pallets[]', 'dry_cartons[]')
        
        # Cube section
        save_section_rows('Cube', 'cube_formula[]', 'cube_packaging_sku[]', 'cube_packaging_sku[]', 'cube_pallets[]', 'cube_cartons[]')
        
        # Tin section
        save_section_rows('Tin', 'tin_formula[]', 'tin_filling_sku[]', 'tin_packaging_sku[]', 'tin_pallets[]', 'tin_cartons[]')
        
        # Sachet & Pouch section
        save_section_rows('Sachet_Pouch', 'sachet_formula[]', 'sachet_filling_sku[]', 'sachet_packaging_sku[]', 'sachet_pallets[]', 'sachet_cartons[]')
        
        db.session.commit()
        log_action('SHIFT_REPORT', f'Shift: {shift}')
        flash('End of shift report submitted!', 'success')
        return redirect(url_for('dashboard'))
    
    # Check if report already submitted for this shift
    existing = EndOfShiftReport.query.filter_by(shift=shift, report_date=today).first()
    
    return render_template('shift_report.html', shift=shift, today=today, submitted=existing is not None)

@app.route('/qc/shift-report/print')
@login_required
def print_shift_report():
    if current_user.role not in ['qc', 'production', 'factory', 'md']:
        flash('Access denied.', 'error')
        return redirect(url_for('dashboard'))
    
    now = datetime.utcnow()
    shift = request.args.get('shift', 'A' if 7 <= now.hour < 19 else 'B')
    date_str = request.args.get('date', now.date().isoformat())
    report_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    
    reports = EndOfShiftReport.query.filter_by(shift=shift, report_date=report_date).order_by(EndOfShiftReport.section).all()
    return render_template('shift_report_print.html', reports=reports, shift=shift, date=report_date)

# ============================================
# LOGOUT
# ============================================

@app.route('/logout')
@login_required
def logout():
    log_action('LOGOUT')
    logout_user()
    flash('You have been logged out.', 'success')
    return redirect(url_for('login'))

# ============================================
# HELPER FUNCTIONS
# ============================================

def _update_batch_size(formula_id):
    ingredients = FormulaIngredient.query.filter_by(formula_id=formula_id).all()
    total = sum(ing.quantity for ing in ingredients)
    formula = Formula.query.get(formula_id)
    if formula:
        formula.batch_size = total
        db.session.commit()

def _generate_batch_number(formula):
    now = datetime.utcnow()
    hours = now.hour
    shift = 'A' if 7 <= hours < 19 else 'B'
    is_tomato = formula and formula.code in ['F-021A', 'F-021B', 'F-021C', 'F-022', 'F-023', 'F-024', 'F-025']
    if is_tomato:
        prefix = now.strftime('%d%m') + '-' + shift + '-'
    else:
        prefix = now.strftime('%m%y') + '-' + shift + '-'
    last_batch = QCTestResult.query.filter(QCTestResult.batch_number.like(prefix + '%')).order_by(QCTestResult.batch_number.desc()).first()
    if last_batch:
        try:
            seq = int(last_batch.batch_number.split('-')[-1]) + 1
        except:
            seq = 1
    else:
        seq = 1
    return prefix + str(seq).zfill(3)

# ============================================
# INGREDIENT MAPPING
# ============================================

INGREDIENT_MAP = {
    'Garri': 'Garri', 'Sugar': 'Sugar', 'Milk': 'Non-dairy creamer (F28)',
    'Non-dairy creamer': 'Non-dairy creamer (F28)', 'Azika creamer': 'Non-dairy creamer (A20)',
    'Azika powder': 'Non-dairy creamer (A20)', 'Azika milk': 'Non-dairy creamer (A20)',
    'Azika': 'Non-dairy creamer (A20)', 'Grinded sugar': 'Sugar',
    'Magnesium stearate': 'Magnesium stearate', 'Cocoa powder': 'Cocoa powder',
    'CMC': 'CMC (Carboxymethyl cellulose)', 'Fibre': 'Soya Fibre',
    'Corn starch': 'Corn starch', 'Silicon dioxide': 'Silicon dioxide',
    'Lecithin': 'Lecithin', 'Maltdextrin': 'Maltodextrin', 'Malt dextrin': 'Maltodextrin',
    'Black tea powder': 'Black tea powder', 'Coffee powder': 'Coffee powder',
    'Vitamins': 'Vitamins', 'Starch': 'Corn starch', 'Wheat': 'Australian wheat',
    'Australian wheat': 'Australian wheat', 'Russian wheat': 'Russian wheat',
    'Rice': 'Rice', 'Ginger with honey premix': 'Ginger with honey premix',
    'Chicken flavor': 'Chicken flavor', 'Beef flavor': 'Chicken flavor',
    'Sea food flavor': 'Sea food flavor', 'Curry flavor': 'Curry flavor',
    'Onion flavor': 'Onion flavor', 'Tomato flavor': 'Tomato flavor',
    'Salt': 'Salt', 'Palm oil': 'Palm oil',
    'M.S.G.': 'M.S.G. (Monosodium glutamate)', 'Msg': 'M.S.G. (Monosodium glutamate)',
    'Chicken oil': 'Chicken oil', 'Chicken powder': 'Chicken powder',
    'Mixed fruit powder': 'Mixed fruit powder', 'Strawberry powder': 'Strawberry powder',
    'Vanilla flavor': 'Vanilla flavor', 'Concentrate': 'Tomato concentrate',
    'Tomato concentrate': 'Tomato concentrate', 'Water': 'Water',
    'Citric acid': 'Citric acid', 'Potassium sorbate': 'Potassium sorbate',
    'Caramel': 'Caramel', 'Colour (Erythrosine)': 'Colour (Erythrosine)',
    'Colour (Ponceau 4R)': 'Colour (Ponceau 4R)', 'Colour (Sunset Yellow)': 'Colour (Sunset Yellow)',
    'Colour (Allura Red)': 'Colour (Allura Red)', 'Stabilizer (CMC)': 'CMC (Carboxymethyl cellulose)',
    'Sodium ascorbate': 'Sodium ascorbate', 'Sodium erythrobate': 'Sodium ascorbate',
    'Garlic': 'Garlic powder', 'Ginger': 'Ginger powder', 'Tumeric': 'Tumeric powder',
    'Onion powder': 'Onion powder', 'Pepper': 'Pepper', 'Acetic acid': 'Acetic acid',
}

# ============================================
# INIT DATABASE
# ============================================

def init_db():
    with app.app_context():
        db.create_all()
        
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
            print("Database created and seeded with test users!")
        
        if RawMaterial.query.count() == 0:
            materials = [
                RawMaterial(code='RM-001', name='Acetic acid', supplier='TBD', unit='kg', cost_per_unit=0, stock_level=0, created_by='system'),
                RawMaterial(code='RM-002', name='Citric acid', supplier='TBD', unit='kg', cost_per_unit=0, stock_level=0, created_by='system'),
                RawMaterial(code='RM-003', name='Potassium sorbate', supplier='TBD', unit='kg', cost_per_unit=0, stock_level=0, created_by='system'),
                RawMaterial(code='RM-004', name='Sodium ascorbate', supplier='TBD', unit='kg', cost_per_unit=0, stock_level=0, created_by='system'),
                RawMaterial(code='RM-005', name='Colour (Erythrosine)', supplier='TBD', unit='kg', cost_per_unit=0, stock_level=0, created_by='system'),
                RawMaterial(code='RM-006', name='Colour (Ponceau 4R)', supplier='TBD', unit='kg', cost_per_unit=0, stock_level=0, created_by='system'),
                RawMaterial(code='RM-007', name='Colour (Sunset Yellow)', supplier='TBD', unit='kg', cost_per_unit=0, stock_level=0, created_by='system'),
                RawMaterial(code='RM-008', name='Colour (Allura Red)', supplier='TBD', unit='kg', cost_per_unit=0, stock_level=0, created_by='system'),
                RawMaterial(code='RM-009', name='Caramel', supplier='TBD', unit='kg', cost_per_unit=0, stock_level=0, created_by='system'),
                RawMaterial(code='RM-010', name='Non-dairy creamer (F28)', supplier='TBD', unit='kg', cost_per_unit=0, stock_level=0, created_by='system'),
                RawMaterial(code='RM-011', name='Non-dairy creamer (A20)', supplier='TBD', unit='kg', cost_per_unit=0, stock_level=0, created_by='system'),
                RawMaterial(code='RM-012', name='Soya Fibre', supplier='TBD', unit='kg', cost_per_unit=0, stock_level=0, created_by='system'),
                RawMaterial(code='RM-013', name='Corn starch', supplier='TBD', unit='kg', cost_per_unit=0, stock_level=0, created_by='system'),
                RawMaterial(code='RM-014', name='Maltodextrin', supplier='TBD', unit='kg', cost_per_unit=0, stock_level=0, created_by='system'),
                RawMaterial(code='RM-015', name='CMC (Carboxymethyl cellulose)', supplier='TBD', unit='kg', cost_per_unit=0, stock_level=0, created_by='system'),
                RawMaterial(code='RM-016', name='Chicken flavor', supplier='TBD', unit='kg', cost_per_unit=0, stock_level=0, created_by='system'),
                RawMaterial(code='RM-017', name='Chicken powder', supplier='TBD', unit='kg', cost_per_unit=0, stock_level=0, created_by='system'),
                RawMaterial(code='RM-018', name='Chicken oil', supplier='TBD', unit='kg', cost_per_unit=0, stock_level=0, created_by='system'),
                RawMaterial(code='RM-019', name='Curry flavor', supplier='TBD', unit='kg', cost_per_unit=0, stock_level=0, created_by='system'),
                RawMaterial(code='RM-020', name='Onion flavor', supplier='TBD', unit='kg', cost_per_unit=0, stock_level=0, created_by='system'),
                RawMaterial(code='RM-021', name='Onion powder', supplier='TBD', unit='kg', cost_per_unit=0, stock_level=0, created_by='system'),
                RawMaterial(code='RM-022', name='Garlic powder', supplier='TBD', unit='kg', cost_per_unit=0, stock_level=0, created_by='system'),
                RawMaterial(code='RM-023', name='M.S.G. (Monosodium glutamate)', supplier='TBD', unit='kg', cost_per_unit=0, stock_level=0, created_by='system'),
                RawMaterial(code='RM-024', name='Sea food flavor', supplier='TBD', unit='kg', cost_per_unit=0, stock_level=0, created_by='system'),
                RawMaterial(code='RM-025', name='Tomato flavor', supplier='TBD', unit='kg', cost_per_unit=0, stock_level=0, created_by='system'),
                RawMaterial(code='RM-026', name='Vanilla flavor', supplier='TBD', unit='kg', cost_per_unit=0, stock_level=0, created_by='system'),
                RawMaterial(code='RM-027', name='Strawberry powder', supplier='TBD', unit='kg', cost_per_unit=0, stock_level=0, created_by='system'),
                RawMaterial(code='RM-028', name='Mixed fruit powder', supplier='TBD', unit='kg', cost_per_unit=0, stock_level=0, created_by='system'),
                RawMaterial(code='RM-029', name='Ginger with honey premix', supplier='TBD', unit='kg', cost_per_unit=0, stock_level=0, created_by='system'),
                RawMaterial(code='RM-030', name='Australian wheat', supplier='TBD', unit='kg', cost_per_unit=0, stock_level=0, created_by='system'),
                RawMaterial(code='RM-031', name='Russian wheat', supplier='TBD', unit='kg', cost_per_unit=0, stock_level=0, created_by='system'),
                RawMaterial(code='RM-032', name='Garri', supplier='TBD', unit='kg', cost_per_unit=0, stock_level=0, created_by='system'),
                RawMaterial(code='RM-033', name='Rice', supplier='TBD', unit='kg', cost_per_unit=0, stock_level=0, created_by='system'),
                RawMaterial(code='RM-034', name='Ginger powder', supplier='TBD', unit='kg', cost_per_unit=0, stock_level=0, created_by='system'),
                RawMaterial(code='RM-035', name='Tumeric powder', supplier='TBD', unit='kg', cost_per_unit=0, stock_level=0, created_by='system'),
                RawMaterial(code='RM-036', name='Pepper', supplier='TBD', unit='kg', cost_per_unit=0, stock_level=0, created_by='system'),
                RawMaterial(code='RM-037', name='Palm oil', supplier='TBD', unit='kg', cost_per_unit=0, stock_level=0, created_by='system'),
                RawMaterial(code='RM-038', name='Lecithin', supplier='TBD', unit='kg', cost_per_unit=0, stock_level=0, created_by='system'),
                RawMaterial(code='RM-039', name='Black tea powder', supplier='TBD', unit='kg', cost_per_unit=0, stock_level=0, created_by='system'),
                RawMaterial(code='RM-040', name='Cocoa powder', supplier='TBD', unit='kg', cost_per_unit=0, stock_level=0, created_by='system'),
                RawMaterial(code='RM-041', name='Coffee powder', supplier='TBD', unit='kg', cost_per_unit=0, stock_level=0, created_by='system'),
                RawMaterial(code='RM-042', name='Magnesium stearate', supplier='TBD', unit='kg', cost_per_unit=0, stock_level=0, created_by='system'),
                RawMaterial(code='RM-043', name='Silicon dioxide', supplier='TBD', unit='kg', cost_per_unit=0, stock_level=0, created_by='system'),
                RawMaterial(code='RM-044', name='Sugar', supplier='TBD', unit='kg', cost_per_unit=0, stock_level=0, created_by='system'),
                RawMaterial(code='RM-045', name='Tomato concentrate', supplier='TBD', unit='kg', cost_per_unit=0, stock_level=0, created_by='system'),
                RawMaterial(code='RM-046', name='Tomato powder', supplier='TBD', unit='kg', cost_per_unit=0, stock_level=0, created_by='system'),
                RawMaterial(code='RM-047', name='Vitamins', supplier='TBD', unit='kg', cost_per_unit=0, stock_level=0, created_by='system'),
                RawMaterial(code='RM-048', name='Salt', supplier='TBD', unit='kg', cost_per_unit=0, stock_level=0, created_by='system'),
                RawMaterial(code='RM-049', name='Water', supplier='TBD', unit='L', cost_per_unit=0, stock_level=0, created_by='system'),
            ]
            db.session.add_all(materials)
            db.session.commit()
            print("49 raw materials seeded!")
        
        if QCParameter.query.count() == 0:
            default_params = [
                QCParameter(name='BRIX', unit='°Bx', spec_min=0, spec_max=100),
                QCParameter(name='COLOUR', unit='', spec_min=None, spec_max=None),
                QCParameter(name='BRIGHTNESS', unit='%', spec_min=0, spec_max=100),
                QCParameter(name='TEMPERATURE', unit='°C', spec_min=0, spec_max=200),
                QCParameter(name='SALT', unit='%', spec_min=0, spec_max=100),
                QCParameter(name='%SALT', unit='%', spec_min=0, spec_max=100),
                QCParameter(name='pH', unit='', spec_min=0, spec_max=14),
                QCParameter(name='ACIDITY', unit='%', spec_min=0, spec_max=100),
                QCParameter(name='BOSTWICK', unit='cm', spec_min=0, spec_max=30),
            ]
            db.session.add_all(default_params)
            db.session.commit()
            print("9 QC parameters seeded!")
        
        _seed_formulas()

def _seed_formulas():
    if Formula.query.count() > 0:
        return
    
    materials = {m.name: m for m in RawMaterial.query.all()}
    
    def get_mat(name):
        mapped = INGREDIENT_MAP.get(name, name)
        return materials.get(mapped)
    
    def add_ings(formula, ing_list):
        for ing_name, qty in ing_list:
            mat = get_mat(ing_name)
            if mat:
                unit = 'L' if mat.name == 'Water' else 'kg'
                db.session.add(FormulaIngredient(formula_id=formula.id, raw_material_id=mat.id, quantity=qty, unit=unit))
    
    # GARRI MIX 3IN 1
    f = Formula(code='F-001-OLD', name='Garri Mix 3in1', version='1.0', status='archived', created_by='system')
    db.session.add(f); db.session.flush()
    add_ings(f, [('Garri', 23.1), ('Sugar', 5), ('Milk', 2.5)])
    f = Formula(code='F-001', name='Garri Mix 3in1', version='2.0', status='approved', created_by='system')
    db.session.add(f); db.session.flush()
    add_ings(f, [('Garri', 20), ('Sugar', 6), ('Milk', 12)])
    
    # RICVITA
    f = Formula(code='F-002-OLD', name='Ricvita', version='1.0', status='archived', created_by='system')
    db.session.add(f); db.session.flush()
    add_ings(f, [('Non-dairy creamer', 50), ('Grinded sugar', 62), ('Magnesium stearate', 1), ('Cocoa powder', 4), ('CMC', 0.25), ('Fibre', 0.25)])
    f = Formula(code='F-002', name='Ricvita', version='2.0', status='approved', created_by='system')
    db.session.add(f); db.session.flush()
    add_ings(f, [('Non-dairy creamer', 65), ('Sugar', 50), ('Magnesium stearate', 1), ('Cocoa powder', 6), ('Corn starch', 5)])
    f = Formula(code='F-002B', name='Ricvita (Alt)', version='2.1', status='approved', created_by='system')
    db.session.add(f); db.session.flush()
    add_ings(f, [('Non-dairy creamer', 65), ('Sugar', 50), ('Lecithin', 6), ('Cocoa powder', 6.5), ('Corn starch', 5), ('Silicon dioxide', 0.7)])
    f = Formula(code='F-002S', name='Small Size Ricvita', version='1.0', status='archived', created_by='system')
    db.session.add(f); db.session.flush()
    add_ings(f, [('Non-dairy creamer', 15), ('Azika creamer', 35), ('Grinded sugar', 62), ('Magnesium stearate', 1), ('Cocoa powder', 4), ('CMC', 0.25), ('Fibre', 0.25)])
    f = Formula(code='F-002BIG', name='Big Size Ricvita', version='1.0', status='archived', created_by='system')
    db.session.add(f); db.session.flush()
    add_ings(f, [('Non-dairy creamer', 50), ('Grinded sugar', 62), ('Magnesium stearate', 1), ('Cocoa powder', 4), ('CMC', 0.25), ('Fibre', 0.25)])
    
    # MILK TEA
    f = Formula(code='F-003-OLD', name='Milk Tea', version='1.0', status='archived', created_by='system')
    db.session.add(f); db.session.flush()
    add_ings(f, [('Non-dairy creamer', 31), ('Sugar', 62), ('Magnesium stearate', 1), ('Maltdextrin', 10), ('CMC', 0.25), ('Fibre', 0.25), ('Black tea powder', 3.5)])
    f = Formula(code='F-003', name='Milk Tea', version='2.0', status='approved', created_by='system')
    db.session.add(f); db.session.flush()
    add_ings(f, [('Non-dairy creamer', 31), ('Sugar', 62), ('Magnesium stearate', 1), ('Maltdextrin', 10), ('Corn starch', 5), ('Black tea powder', 3.5)])
    f = Formula(code='F-003B', name='Milk Tea (Alt)', version='2.1', status='approved', created_by='system')
    db.session.add(f); db.session.flush()
    add_ings(f, [('Non-dairy creamer', 31), ('Sugar', 62), ('Silicon dioxide', 0.7), ('Maltdextrin', 10), ('Corn starch', 5), ('Black tea powder', 3.5), ('Lecithin', 2)])
    f = Formula(code='F-003S', name='Small Size Milk Tea', version='1.0', status='archived', created_by='system')
    db.session.add(f); db.session.flush()
    add_ings(f, [('Black tea powder', 3.5), ('Sugar', 62), ('Malt dextrin', 10), ('Azika creamer', 21.7), ('Non-dairy creamer', 9.3), ('Magnesium stearate', 1), ('CMC', 0.25), ('Fibre', 0.25)])
    f = Formula(code='F-003BIG', name='Big Size Milk Tea', version='1.0', status='archived', created_by='system')
    db.session.add(f); db.session.flush()
    add_ings(f, [('Black tea powder', 3.5), ('Sugar', 62), ('Malt dextrin', 10), ('Magnesium stearate', 1), ('Non-dairy creamer', 31), ('CMC', 0.25), ('Fibre', 0.25)])
    
    # COFFEE MIX
    f = Formula(code='F-004-OLD', name='Coffee Mix', version='1.0', status='archived', created_by='system')
    db.session.add(f); db.session.flush()
    add_ings(f, [('Non-dairy creamer', 31), ('Sugar', 62), ('Magnesium stearate', 1), ('Maltdextrin', 10), ('CMC', 0.25), ('Fibre', 0.25), ('Coffee powder', 4.5)])
    f = Formula(code='F-004', name='Coffee Mix', version='2.0', status='approved', created_by='system')
    db.session.add(f); db.session.flush()
    add_ings(f, [('Non-dairy creamer', 31), ('Sugar', 62), ('Magnesium stearate', 1), ('Maltdextrin', 10), ('Corn starch', 5), ('Coffee powder', 4.5)])
    f = Formula(code='F-004S', name='Small Size Coffee Mix', version='1.0', status='archived', created_by='system')
    db.session.add(f); db.session.flush()
    add_ings(f, [('Coffee powder', 4.5), ('Azika powder', 21.7), ('Non-dairy creamer', 9.3), ('Sugar', 44), ('Malt dextrin', 10), ('Magnesium stearate', 1), ('CMC', 0.25), ('Fibre', 0.25)])
    
    # MILK POWDER
    f = Formula(code='F-005', name='Milk Powder', version='1.0', status='approved', created_by='system')
    db.session.add(f); db.session.flush()
    add_ings(f, [('Non-dairy creamer', 50), ('Vitamins', 0.2)])
    f = Formula(code='F-005S', name='Small Size Milk Powder', version='1.0', status='archived', created_by='system')
    db.session.add(f); db.session.flush()
    add_ings(f, [('Azika milk', 42.5), ('Non-dairy creamer', 7.5), ('Vitamins', 0.2)])
    f = Formula(code='F-005BIG', name='Big Size Milk Powder', version='1.0', status='archived', created_by='system')
    db.session.add(f); db.session.flush()
    add_ings(f, [('Non-dairy creamer', 50), ('Vitamins', 0.2)])
    
    # GARRI CHOCOLATE MIX
    f = Formula(code='F-006', name='Garri Chocolate Mix', version='1.0', status='approved', created_by='system')
    db.session.add(f); db.session.flush()
    add_ings(f, [('Garri', 23.1), ('Sugar', 6), ('Milk', 8), ('Cocoa powder', 1)])
    
    # WHEAT FLOUR
    f = Formula(code='F-007-OLD', name='Wheat Flour', version='1.0', status='archived', created_by='system')
    db.session.add(f); db.session.flush()
    add_ings(f, [('Australian wheat', 22.5), ('Russian wheat', 22.5), ('Starch', 5), ('Vitamins', 0.5)])
    f = Formula(code='F-007', name='Wheat Flour', version='2.0', status='approved', created_by='system')
    db.session.add(f); db.session.flush()
    add_ings(f, [('Wheat', 45), ('Starch', 5), ('Vitamins', 0.5)])
    
    # RICE FLOUR
    f = Formula(code='F-008', name='Rice Flour', version='1.0', status='approved', created_by='system')
    db.session.add(f); db.session.flush()
    add_ings(f, [('Rice', 1)])
    
    # GINGER WITH HONEY TEA
    f = Formula(code='F-009', name='Ginger with Honey Tea', version='1.0', status='approved', created_by='system')
    db.session.add(f); db.session.flush()
    add_ings(f, [('Ginger with honey premix', 1)])
    
    # CUBE SEASONINGS
    cube_formulas = [
        ('F-010', 'Chicken Flavor Seasoning', [('Chicken flavor', 4), ('Salt', 50), ('Sugar', 11), ('Palm oil', 3), ('Corn starch', 1), ('Maltdextrin', 4), ('M.S.G.', 12), ('Magnesium stearate', 1), ('Chicken oil', 0.2)]),
        ('F-011', 'Beef Flavor Seasoning', [('Beef flavor', 4), ('Salt', 50), ('Sugar', 11), ('Palm oil', 3), ('Corn starch', 1), ('Maltdextrin', 4), ('M.S.G.', 12), ('Magnesium stearate', 1)]),
        ('F-012', 'Sea Food Flavor Seasoning', [('Sea food flavor', 4), ('Salt', 50), ('Sugar', 11), ('Palm oil', 3), ('Corn starch', 1), ('Maltdextrin', 4), ('M.S.G.', 12), ('Magnesium stearate', 3)]),
        ('F-013', 'Curry Flavor Seasoning', [('Curry flavor', 1), ('Salt', 50), ('Sugar', 11), ('Palm oil', 3), ('Corn starch', 1), ('Maltdextrin', 4), ('M.S.G.', 12), ('Magnesium stearate', 1)]),
        ('F-014', 'Onion Flavor Seasoning', [('Onion flavor', 4), ('Salt', 50), ('Sugar', 11), ('Palm oil', 3), ('Corn starch', 1), ('Maltdextrin', 4), ('M.S.G.', 12), ('Magnesium stearate', 1)]),
        ('F-015', 'Tomato Flavor Seasoning', [('Tomato flavor', 4), ('Salt', 50), ('Sugar', 11), ('Palm oil', 3), ('Corn starch', 1), ('Maltdextrin', 4), ('M.S.G.', 12), ('Magnesium stearate', 1)]),
        ('F-016', 'Milk Cubes', [('Non-dairy creamer', 60), ('Azika', 5), ('Sugar', 35), ('Magnesium stearate', 0.5)]),
        ('F-017', 'Chocolate Cubes', [('Non-dairy creamer', 60), ('Azika', 5), ('Sugar', 35), ('Cocoa powder', 3), ('Magnesium stearate', 0.5)]),
        ('F-018', 'Mixed Fruits Cubes', [('Non-dairy creamer', 60), ('Azika', 5), ('Sugar', 35), ('Magnesium stearate', 0.5), ('Mixed fruit powder', 0.5)]),
        ('F-019', 'Strawberry Cubes', [('Non-dairy creamer', 60), ('Azika', 60), ('Sugar', 35), ('Magnesium stearate', 0.5), ('Strawberry powder', 0.5)]),
        ('F-020', 'Vanilla Cubes', [('Non-dairy creamer', 60), ('Azika', 5), ('Sugar', 35), ('Magnesium stearate', 0.5), ('Vanilla flavor', 0.5)]),
    ]
    for code, name, ings in cube_formulas:
        f = Formula(code=code, name=name, version='1.0', status='approved', created_by='system')
        db.session.add(f); db.session.flush()
        add_ings(f, ings)
    
    # TOMATO PASTES
    tomato_021_ings = [('Concentrate', 480), ('Water', 1960), ('Fibre', 180), ('Sugar', 140), ('Salt', 50), ('Citric acid', 14.3), ('Potassium sorbate', 8.7), ('Caramel', 1), ('Msg', 1), ('Colour (Erythrosine)', 0.06), ('Colour (Ponceau 4R)', 0.074), ('Colour (Sunset Yellow)', 0.06), ('Colour (Allura Red)', 0.008), ('Stabilizer (CMC)', 0.067), ('Starch', 5), ('Maltdextrin', 6), ('Sodium ascorbate', 0.2)]
    
    f = Formula(code='F-021A', name='Ric-giko Tomato Paste', version='1.0', status='approved', created_by='system')
    db.session.add(f); db.session.flush(); add_ings(f, tomato_021_ings)
    f = Formula(code='F-021B', name='Tomagood Tomato Mix', version='1.0', status='approved', created_by='system')
    db.session.add(f); db.session.flush(); add_ings(f, tomato_021_ings)
    f = Formula(code='F-021C', name='Erisco (Normal) Tomato Paste', version='1.0', status='approved', created_by='system')
    db.session.add(f); db.session.flush(); add_ings(f, tomato_021_ings)
    
    f = Formula(code='F-022', name='Erisco Tomato Paste', version='1.0', status='approved', created_by='system')
    db.session.add(f); db.session.flush()
    add_ings(f, [('Concentrate', 720), ('Water', 1960), ('Fibre', 180), ('Sugar', 150), ('Salt', 60), ('Citric acid', 14.3), ('Potassium sorbate', 8.7), ('Caramel', 6), ('Msg', 1), ('Colour (Erythrosine)', 0.06), ('Colour (Ponceau 4R)', 0.075), ('Colour (Sunset Yellow)', 0.061), ('Colour (Allura Red)', 0.009), ('Stabilizer (CMC)', 0.067), ('Maltdextrin', 6), ('Sodium ascorbate', 2)])
    f = Formula(code='F-023', name='Nagiko Tomato Mix', version='1.0', status='approved', created_by='system')
    db.session.add(f); db.session.flush()
    add_ings(f, [('Tomato concentrate', 330), ('Water', 2110), ('Fibre', 200), ('Sugar', 75), ('Salt', 40), ('Citric acid', 14.3), ('Potassium sorbate', 8.7), ('Caramel', 3), ('Msg', 1), ('Colour (Erythrosine)', 0.06), ('Colour (Ponceau 4R)', 0.074), ('Colour (Sunset Yellow)', 0.06), ('Colour (Allura Red)', 0.008), ('Stabilizer (CMC)', 0.067), ('Starch', 5), ('Maltdextrin', 6)])
    f = Formula(code='F-024', name='Erisco Party Jollof Tomato Paste', version='1.0', status='approved', created_by='system')
    db.session.add(f); db.session.flush()
    add_ings(f, [('Concentrate', 480), ('Water', 1960), ('Chicken powder', 8), ('Chicken oil', 0.5), ('Garlic', 6), ('Ginger', 5), ('Tumeric', 5), ('Onion powder', 40), ('Fibre', 180), ('Sugar', 140), ('Salt', 50), ('Citric acid', 14.3), ('Potassium sorbate', 8.7), ('Caramel', 1), ('Msg', 25), ('Colour (Sunset Yellow)', 0.157), ('Colour (Allura Red)', 0.208), ('Stabilizer (CMC)', 0.067), ('Starch', 5), ('Maltdextrin', 6), ('Sodium erythrobate', 0.2), ('Pepper', 8), ('Palm oil', 3)])
    f = Formula(code='F-025', name='Erisco So Red Ketchup', version='1.0', status='approved', created_by='system')
    db.session.add(f); db.session.flush()
    add_ings(f, [('Tomato concentrate', 233), ('Fibre', 30), ('Sugar', 253), ('Salt', 30), ('Citric acid', 10), ('Potassium sorbate', 4.5), ('Onion powder', 3), ('Colour (Erythrosine)', 0.035), ('Colour (Ponceau 4R)', 0.015), ('Acetic acid', 5), ('Corn starch', 20), ('Water', 940)])
    
    for formula in Formula.query.all():
        total = sum(ing.quantity for ing in formula.ingredients)
        formula.batch_size = total
    db.session.commit()
    print(f"{Formula.query.count()} formulas seeded!")

if __name__ == '__main__':
    init_db()
    app.run(debug=True, host='0.0.0.0', port=5000)