from flask import Flask, render_template, redirect, url_for, request, flash, Response
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import json
import csv
import io
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

# ============================================
# DATABASE MODELS
# ============================================

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    display_name = db.Column(db.String(100), nullable=False)

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
    status = db.Column(db.String(20), default='draft')  # draft, pending_approval, approved, archived
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

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# ============================================
# HELPER: Check if user can see formula details
# ============================================
def can_view_formula_details():
    """Only R&D and MD can see ingredient names and quantities."""
    return current_user.role in ['rd', 'md']

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
    
    # ============================================
    # QC ROLE
    # ============================================
    if current_user.role == 'qc':
        formulas = Formula.query.filter_by(status='approved').all()
        recent_tests = QCTestResult.query.order_by(QCTestResult.test_date.desc()).limit(20).all()
        qc_parameters = QCParameter.query.filter_by(is_active=True).order_by(QCParameter.name).all()
        
        tests_json = []
        for t in recent_tests:
            tests_json.append({
                'id': t.id,
                'batch': t.batch_number,
                'formula_id': t.formula_id,
                'formula_code': t.formula.code if t.formula else 'N/A',
                'date': t.test_date.strftime('%Y-%m-%d') if t.test_date else '',
                'status': t.status,
                'parameters': json.loads(t.parameters) if t.parameters else []
            })
        
        return render_template(
            template, 
            formulas=formulas, 
            recent_tests=recent_tests, 
            qc_parameters=qc_parameters,
            recent_tests_json=json.dumps(tests_json),
            show_formula_details=False
        )
    
    # ============================================
    # R&D ROLE
    # ============================================
    elif current_user.role == 'rd':
        formulas = Formula.query.order_by(Formula.created_at.desc()).all()
        materials = RawMaterial.query.order_by(RawMaterial.name).all()
        qc_feed = QCTestResult.query.order_by(QCTestResult.test_date.desc()).limit(20).all()
        qc_parameters = QCParameter.query.filter_by(is_active=True).order_by(QCParameter.name).all()
        
        all_tests = QCTestResult.query.order_by(QCTestResult.test_date.asc()).all()
        tests_json = []
        for t in all_tests:
            tests_json.append({
                'id': t.id,
                'batch': t.batch_number,
                'formula_id': t.formula_id,
                'formula_code': t.formula.code if t.formula else 'N/A',
                'date': t.test_date.strftime('%Y-%m-%d') if t.test_date else '',
                'status': t.status,
                'parameters': json.loads(t.parameters) if t.parameters else []
            })
        
        params_json = []
        for p in qc_parameters:
            params_json.append({
                'name': p.name,
                'unit': p.unit or '',
                'spec_min': p.spec_min,
                'spec_max': p.spec_max
            })
        
        return render_template(
            template, 
            formulas=formulas, 
            materials=materials, 
            qc_feed=qc_feed,
            qc_parameters_json=json.dumps(params_json),
            all_tests_json=json.dumps(tests_json),
            show_formula_details=True
        )
    
    # ============================================
    # PLANNER ROLE
    # ============================================
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
                            consumption_data[key] = {
                                'material_code': material.code,
                                'material_name': material.name,
                                'unit': ingredient.unit,
                                'total_used': 0,
                                'batch_count': 0,
                                'batches': []
                            }
                        consumption_data[key]['total_used'] += ingredient.quantity
                        consumption_data[key]['batch_count'] += 1
                        consumption_data[key]['batches'].append({
                            'batch_number': batch.batch_number,
                            'formula_code': batch.formula.code,
                            'quantity': ingredient.quantity,
                            'date': batch.test_date.strftime('%d-%b-%Y') if batch.test_date else ''
                        })
        
        production_batches = ProductionBatch.query.order_by(ProductionBatch.planned_date.desc()).all()
        approved_formulas = Formula.query.filter_by(status='approved').all()
        
        return render_template(
            template,
            qc_batches=qc_batches,
            consumption_data=consumption_data,
            production_batches=production_batches,
            approved_formulas=approved_formulas,
            show_formula_details=False
        )
    
    # ============================================
    # PRODUCTION MANAGER ROLE
    # ============================================
    elif current_user.role == 'production':
        qc_feed = QCTestResult.query.order_by(QCTestResult.test_date.desc()).limit(15).all()
        production_batches = ProductionBatch.query.order_by(ProductionBatch.planned_date.desc()).all()
        
        total_produced = sum(b.quantity_produced or 0 for b in production_batches if b.status == 'completed')
        total_planned = sum(b.quantity_planned or 0 for b in production_batches)
        batches_today = len([b for b in production_batches
                           if b.planned_date and b.planned_date.date() == datetime.utcnow().date()])
        qc_pass_rate = 0
        if qc_feed:
            passed = len([t for t in qc_feed if t.status == 'pass'])
            qc_pass_rate = round((passed / len(qc_feed)) * 100)
        
        return render_template(
            template,
            qc_feed=qc_feed,
            production_batches=production_batches,
            total_produced=total_produced,
            total_planned=total_planned,
            batches_today=batches_today,
            qc_pass_rate=qc_pass_rate,
            show_formula_details=False
        )
    
    # ============================================
    # FACTORY MANAGER ROLE
    # ============================================
    elif current_user.role == 'factory':
        production_batches = ProductionBatch.query.order_by(ProductionBatch.planned_date.desc()).limit(20).all()
        qc_summary = QCTestResult.query.order_by(QCTestResult.test_date.desc()).limit(20).all()
        
        total_qc_tests = len(qc_summary)
        passed_qc = len([t for t in qc_summary if t.status == 'pass'])
        failed_qc = len([t for t in qc_summary if t.status == 'fail'])
        
        completed_batches = [b for b in production_batches if b.status == 'completed']
        efficiency = 0
        if completed_batches:
            efficiencies = []
            for b in completed_batches:
                if b.quantity_planned and b.quantity_planned > 0:
                    efficiencies.append((b.quantity_produced or 0) / b.quantity_planned * 100)
            if efficiencies:
                efficiency = round(sum(efficiencies) / len(efficiencies))
        
        low_stock_materials = RawMaterial.query.filter(RawMaterial.stock_level < 100).all()
        
        return render_template(
            template,
            production_batches=production_batches,
            qc_summary=qc_summary,
            total_qc_tests=total_qc_tests,
            passed_qc=passed_qc,
            failed_qc=failed_qc,
            efficiency=efficiency,
            low_stock_materials=low_stock_materials,
            show_formula_details=False
        )
    
    # ============================================
    # AUDIT ROLE
    # ============================================
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
        
        return render_template(
            template,
            qc_audit=qc_audit,
            production_audit=production_audit,
            formulas=formulas,
            total_tests=total_tests,
            passed_tests=passed_tests,
            failed_tests=failed_tests,
            pending_tests=pending_tests,
            testers=testers,
            show_formula_details=False
        )
    
    # ============================================
    # MD (EXECUTIVE) ROLE
    # ============================================
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
        
        dept_activity = {
            'R&D': Formula.query.count(),
            'QC': total_qc_tests,
            'Planning': total_batches,
            'Production': completed_batches
        }
        
        return render_template(
            template,
            total_formulas=total_formulas,
            approved_formulas=approved_formulas,
            pending_approvals=pending_approvals,
            total_materials=total_materials,
            total_qc_tests=total_qc_tests,
            qc_pass_rate=qc_pass_rate,
            monthly_qc_count=len(monthly_qc),
            monthly_pass=monthly_pass,
            monthly_fail=monthly_fail,
            total_batches=total_batches,
            completed_batches=completed_batches,
            total_produced=total_produced,
            total_planned=total_planned,
            production_efficiency=production_efficiency,
            total_inventory_value=total_inventory_value,
            low_stock_count=low_stock_count,
            recent_qc=recent_qc,
            recent_production=recent_production,
            dept_activity=dept_activity,
            failed_tests=len([t for t in all_qc if t.status == 'fail']),
            pending_formulas=pending_formulas,
            show_formula_details=True
        )
    
    return render_template(template, show_formula_details=False)

# ============================================
# QC ROUTE
# ============================================

@app.route('/qc/submit', methods=['POST'])
@login_required
def submit_qc_result():
    if current_user.role != 'qc':
        flash('Access denied')
        return redirect(url_for('dashboard'))
    
    formula_id = request.form.get('formula_id')
    batch_number = request.form.get('batch_number')
    test_date = request.form.get('test_date')
    notes = request.form.get('notes')
    
    param_names = request.form.getlist('param_name[]')
    param_results = request.form.getlist('param_result[]')
    param_units = request.form.getlist('param_unit[]')
    param_specs = request.form.getlist('param_spec[]')
    
    parameters = []
    all_pass = True
    for i in range(len(param_names)):
        if param_names[i]:
            spec_range = param_specs[i] if i < len(param_specs) else ''
            result_val = param_results[i] if i < len(param_results) else ''
            
            param_pass = True
            if spec_range and result_val:
                try:
                    low, high = spec_range.split('-')
                    result_float = float(result_val)
                    param_pass = float(low) <= result_float <= float(high)
                except:
                    param_pass = True
            
            if not param_pass:
                all_pass = False
            
            parameters.append({
                'name': param_names[i],
                'result': param_results[i] if i < len(param_results) else '',
                'unit': param_units[i] if i < len(param_units) else '',
                'spec': spec_range,
                'pass': param_pass
            })
    
    test_result = QCTestResult(
        formula_id=formula_id,
        batch_number=batch_number,
        test_date=datetime.strptime(test_date, '%Y-%m-%d') if test_date else datetime.utcnow(),
        tested_by=current_user.display_name,
        parameters=json.dumps(parameters),
        status='pass' if all_pass else 'fail',
        notes=notes
    )
    
    db.session.add(test_result)
    db.session.commit()
    
    flash('Test result submitted successfully!')
    return redirect(url_for('dashboard'))

# ============================================
# R&D ROUTES
# ============================================

@app.route('/rd/create-formula', methods=['POST'])
@login_required
def create_formula():
    if current_user.role != 'rd':
        flash('Access denied')
        return redirect(url_for('dashboard'))
    
    code = request.form.get('code')
    name = request.form.get('name')
    version = request.form.get('version', '1.0')
    
    existing = Formula.query.filter_by(code=code).first()
    if existing:
        flash('Formula code already exists!')
        return redirect(url_for('dashboard'))
    
    formula = Formula(
        code=code,
        name=name,
        version=version,
        status='draft',
        created_by=current_user.display_name
    )
    db.session.add(formula)
    db.session.commit()
    
    flash(f'Formula {code} created successfully!')
    return redirect(url_for('dashboard'))

@app.route('/rd/add-ingredient', methods=['POST'])
@login_required
def add_ingredient():
    if current_user.role != 'rd':
        flash('Access denied')
        return redirect(url_for('dashboard'))
    
    formula_id = request.form.get('formula_id')
    material_id = request.form.get('material_id')
    quantity = request.form.get('quantity')
    unit = request.form.get('unit', 'kg')
    
    existing = FormulaIngredient.query.filter_by(
        formula_id=formula_id,
        raw_material_id=material_id
    ).first()
    
    if existing:
        flash('This material is already in the formula. Edit it instead.')
        return redirect(url_for('dashboard'))
    
    ingredient = FormulaIngredient(
        formula_id=formula_id,
        raw_material_id=material_id,
        quantity=float(quantity),
        unit=unit
    )
    db.session.add(ingredient)
    db.session.commit()
    
    _update_batch_size(formula_id)
    
    flash('Ingredient added to formula!')
    return redirect(url_for('dashboard'))

@app.route('/rd/update-ingredient/<int:ingredient_id>', methods=['POST'])
@login_required
def update_ingredient(ingredient_id):
    if current_user.role != 'rd':
        flash('Access denied')
        return redirect(url_for('dashboard'))
    
    ingredient = FormulaIngredient.query.get_or_404(ingredient_id)
    ingredient.quantity = float(request.form.get('quantity'))
    ingredient.unit = request.form.get('unit', 'kg')
    db.session.commit()
    
    _update_batch_size(ingredient.formula_id)
    
    flash('Ingredient updated!')
    return redirect(url_for('dashboard'))

@app.route('/rd/remove-ingredient/<int:ingredient_id>', methods=['POST'])
@login_required
def remove_ingredient(ingredient_id):
    if current_user.role != 'rd':
        flash('Access denied')
        return redirect(url_for('dashboard'))
    
    ingredient = FormulaIngredient.query.get_or_404(ingredient_id)
    fid = ingredient.formula_id
    db.session.delete(ingredient)
    db.session.commit()
    
    _update_batch_size(fid)
    
    flash('Ingredient removed from formula.')
    return redirect(url_for('dashboard'))

@app.route('/rd/submit-for-approval/<int:formula_id>', methods=['POST'])
@login_required
def submit_for_approval(formula_id):
    if current_user.role != 'rd':
        flash('Access denied')
        return redirect(url_for('dashboard'))
    
    formula = Formula.query.get_or_404(formula_id)
    if formula.status == 'draft' and formula.ingredients:
        formula.status = 'pending_approval'
        db.session.commit()
        flash(f'Formula {formula.code} submitted for MD approval!')
    else:
        flash('Formula must be in draft status and have ingredients.')
    
    return redirect(url_for('dashboard'))

# ============================================
# MD APPROVAL ROUTES
# ============================================

@app.route('/md/approve/<int:formula_id>', methods=['POST'])
@login_required
def approve_formula(formula_id):
    if current_user.role != 'md':
        flash('Access denied')
        return redirect(url_for('dashboard'))
    
    formula = Formula.query.get_or_404(formula_id)
    if formula.status == 'pending_approval':
        formula.status = 'approved'
        formula.approved_by = current_user.display_name
        formula.approved_at = datetime.utcnow()
        db.session.commit()
        flash(f'Formula {formula.code} has been APPROVED!')
    else:
        flash('Formula is not pending approval.')
    
    return redirect(url_for('dashboard'))

@app.route('/md/reject/<int:formula_id>', methods=['POST'])
@login_required
def reject_formula(formula_id):
    if current_user.role != 'md':
        flash('Access denied')
        return redirect(url_for('dashboard'))
    
    formula = Formula.query.get_or_404(formula_id)
    if formula.status == 'pending_approval':
        formula.status = 'draft'
        db.session.commit()
        flash(f'Formula {formula.code} has been REJECTED and returned to draft.')
    else:
        flash('Formula is not pending approval.')
    
    return redirect(url_for('dashboard'))

@app.route('/rd/update-formula-status/<int:formula_id>', methods=['POST'])
@login_required
def update_formula_status(formula_id):
    if current_user.role != 'rd':
        flash('Access denied')
        return redirect(url_for('dashboard'))
    
    formula = Formula.query.get_or_404(formula_id)
    new_status = request.form.get('status')
    
    if new_status in ['draft', 'archived']:
        formula.status = new_status
        db.session.commit()
        flash(f'Formula {formula.code} is now {new_status.upper()}')
    
    return redirect(url_for('dashboard'))

@app.route('/rd/create-material', methods=['POST'])
@login_required
def create_material():
    if current_user.role != 'rd':
        flash('Access denied')
        return redirect(url_for('dashboard'))
    
    code = request.form.get('code')
    existing = RawMaterial.query.filter_by(code=code).first()
    if existing:
        flash('Material code already exists!')
        return redirect(url_for('dashboard'))
    
    material = RawMaterial(
        code=code,
        name=request.form.get('name'),
        supplier=request.form.get('supplier'),
        unit=request.form.get('unit', 'kg'),
        cost_per_unit=float(request.form.get('cost_per_unit', 0)),
        stock_level=float(request.form.get('stock_level', 0)),
        created_by=current_user.display_name
    )
    db.session.add(material)
    db.session.commit()
    
    flash(f'Material {code} created!')
    return redirect(url_for('dashboard'))

@app.route('/rd/update-material/<int:material_id>', methods=['POST'])
@login_required
def update_material(material_id):
    if current_user.role != 'rd':
        flash('Access denied')
        return redirect(url_for('dashboard'))
    
    material = RawMaterial.query.get_or_404(material_id)
    material.name = request.form.get('name')
    material.supplier = request.form.get('supplier')
    material.unit = request.form.get('unit', 'kg')
    material.cost_per_unit = float(request.form.get('cost_per_unit', 0))
    material.stock_level = float(request.form.get('stock_level', 0))
    db.session.commit()
    
    flash(f'Material {material.code} updated!')
    return redirect(url_for('dashboard'))

# ============================================
# QC PARAMETER MANAGEMENT (R&D access)
# ============================================

@app.route('/rd/parameters')
@login_required
def manage_parameters():
    if current_user.role not in ['rd', 'qc']:
        flash('Access denied')
        return redirect(url_for('dashboard'))
    parameters = QCParameter.query.order_by(QCParameter.name).all()
    return render_template('parameters.html', parameters=parameters)

@app.route('/rd/parameters/add', methods=['POST'])
@login_required
def add_parameter():
    if current_user.role != 'rd':
        flash('Access denied')
        return redirect(url_for('dashboard'))
    
    name = request.form.get('name')
    unit = request.form.get('unit', '')
    spec_min = request.form.get('spec_min')
    spec_max = request.form.get('spec_max')
    
    existing = QCParameter.query.filter_by(name=name).first()
    if existing:
        flash(f'Parameter "{name}" already exists!')
        return redirect(url_for('manage_parameters'))
    
    param = QCParameter(
        name=name,
        unit=unit,
        spec_min=float(spec_min) if spec_min else None,
        spec_max=float(spec_max) if spec_max else None,
        is_active=True
    )
    db.session.add(param)
    db.session.commit()
    
    flash(f'Parameter "{name}" added!')
    return redirect(url_for('manage_parameters'))

@app.route('/rd/parameters/toggle/<int:param_id>', methods=['POST'])
@login_required
def toggle_parameter(param_id):
    if current_user.role != 'rd':
        flash('Access denied')
        return redirect(url_for('dashboard'))
    
    param = QCParameter.query.get_or_404(param_id)
    param.is_active = not param.is_active
    db.session.commit()
    
    status = 'activated' if param.is_active else 'deactivated'
    flash(f'Parameter "{param.name}" {status}!')
    return redirect(url_for('manage_parameters'))

# ============================================
# PLANNER ROUTES
# ============================================

@app.route('/planner/create-batch', methods=['POST'])
@login_required
def create_production_batch():
    if current_user.role != 'planner':
        flash('Access denied')
        return redirect(url_for('dashboard'))
    
    formula_id = request.form.get('formula_id')
    batch_number = request.form.get('batch_number')
    planned_date = request.form.get('planned_date')
    quantity_planned = request.form.get('quantity_planned')
    notes = request.form.get('notes')
    
    existing = ProductionBatch.query.filter_by(batch_number=batch_number).first()
    if existing:
        flash('Batch number already exists!')
        return redirect(url_for('dashboard'))
    
    batch = ProductionBatch(
        formula_id=formula_id,
        batch_number=batch_number,
        planned_date=datetime.strptime(planned_date, '%Y-%m-%d') if planned_date else datetime.utcnow(),
        quantity_planned=float(quantity_planned) if quantity_planned else 0,
        status='planned',
        notes=notes,
        created_by=current_user.display_name
    )
    db.session.add(batch)
    db.session.commit()
    
    flash(f'Production batch {batch_number} planned!')
    return redirect(url_for('dashboard'))

@app.route('/planner/update-batch/<int:batch_id>', methods=['POST'])
@login_required
def update_production_batch(batch_id):
    if current_user.role != 'planner':
        flash('Access denied')
        return redirect(url_for('dashboard'))
    
    batch = ProductionBatch.query.get_or_404(batch_id)
    
    batch.quantity_produced = float(request.form.get('quantity_produced', 0))
    new_status = request.form.get('status')
    if new_status in ['planned', 'in_progress', 'completed', 'cancelled']:
        batch.status = new_status
        if new_status == 'completed':
            batch.actual_date = datetime.utcnow()
    
    db.session.commit()
    flash(f'Batch {batch.batch_number} updated!')
    return redirect(url_for('dashboard'))

@app.route('/planner/export-consumption')
@login_required
def export_consumption():
    if current_user.role != 'planner':
        flash('Access denied')
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
                        consumption[key] = {
                            'name': ing.material.name,
                            'total': 0,
                            'unit': ing.unit,
                            'count': 0
                        }
                    consumption[key]['total'] += ing.quantity
                    consumption[key]['count'] += 1
    
    for code, data in consumption.items():
        writer.writerow([code, data['name'], data['total'], data['unit'], data['count']])
    
    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-disposition": "attachment; filename=consumption_report.csv"}
    )

# ============================================
# LOGOUT
# ============================================

@app.route('/logout')
@login_required
def logout():
    logout_user()
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

# ============================================
# INGREDIENT MAPPING
# ============================================

INGREDIENT_MAP = {
    'Garri': 'Garri',
    'Sugar': 'Sugar',
    'Milk': 'Non-dairy creamer (F28)',
    'Non-dairy creamer': 'Non-dairy creamer (F28)',
    'Azika creamer': 'Non-dairy creamer (A20)',
    'Azika powder': 'Non-dairy creamer (A20)',
    'Azika milk': 'Non-dairy creamer (A20)',
    'Azika': 'Non-dairy creamer (A20)',
    'Grinded sugar': 'Sugar',
    'Magnesium stearate': 'Magnesium stearate',
    'Cocoa powder': 'Cocoa powder',
    'CMC': 'CMC (Carboxymethyl cellulose)',
    'Fibre': 'Soya Fibre',
    'Corn starch': 'Corn starch',
    'Silicon dioxide': 'Silicon dioxide',
    'Lecithin': 'Lecithin',
    'Maltdextrin': 'Maltodextrin',
    'Malt dextrin': 'Maltodextrin',
    'Black tea powder': 'Black tea powder',
    'Coffee powder': 'Coffee powder',
    'Vitamins': 'Vitamins',
    'Starch': 'Corn starch',
    'Wheat': 'Australian wheat',
    'Australian wheat': 'Australian wheat',
    'Russian wheat': 'Russian wheat',
    'Rice': 'Rice',
    'Ginger with honey premix': 'Ginger with honey premix',
    'Chicken flavor': 'Chicken flavor',
    'Beef flavor': 'Chicken flavor',
    'Sea food flavor': 'Sea food flavor',
    'Curry flavor': 'Curry flavor',
    'Onion flavor': 'Onion flavor',
    'Tomato flavor': 'Tomato flavor',
    'Salt': 'Salt',
    'Palm oil': 'Palm oil',
    'M.S.G.': 'M.S.G. (Monosodium glutamate)',
    'Msg': 'M.S.G. (Monosodium glutamate)',
    'Chicken oil': 'Chicken oil',
    'Chicken powder': 'Chicken powder',
    'Mixed fruit powder': 'Mixed fruit powder',
    'Strawberry powder': 'Strawberry powder',
    'Vanilla flavor': 'Vanilla flavor',
    'Concentrate': 'Tomato concentrate',
    'Tomato concentrate': 'Tomato concentrate',
    'Water': 'Water',
    'Citric acid': 'Citric acid',
    'Potassium sorbate': 'Potassium sorbate',
    'Caramel': 'Caramel',
    'Colour (Erythrosine)': 'Colour (Erythrosine)',
    'Colour (Ponceau 4R)': 'Colour (Ponceau 4R)',
    'Colour (Sunset Yellow)': 'Colour (Sunset Yellow)',
    'Colour (Allura Red)': 'Colour (Allura Red)',
    'Stabilizer (CMC)': 'CMC (Carboxymethyl cellulose)',
    'Sodium ascorbate': 'Sodium ascorbate',
    'Sodium erythrobate': 'Sodium ascorbate',
    'Garlic': 'Garlic powder',
    'Ginger': 'Ginger powder',
    'Tumeric': 'Tumeric powder',
    'Onion powder': 'Onion powder',
    'Pepper': 'Pepper',
    'Acetic acid': 'Acetic acid',
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
                db.session.add(FormulaIngredient(
                    formula_id=formula.id,
                    raw_material_id=mat.id,
                    quantity=qty,
                    unit=unit
                ))
    
    # ============================================
    # GARRI MIX 3IN 1
    # ============================================
    f = Formula(code='F-001-OLD', name='Garri Mix 3in1', version='1.0', status='archived', created_by='system')
    db.session.add(f); db.session.flush()
    add_ings(f, [('Garri', 23.1), ('Sugar', 5), ('Milk', 2.5)])
    
    f = Formula(code='F-001', name='Garri Mix 3in1', version='2.0', status='approved', created_by='system')
    db.session.add(f); db.session.flush()
    add_ings(f, [('Garri', 20), ('Sugar', 6), ('Milk', 12)])
    
    # ============================================
    # RICVITA
    # ============================================
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
    
    # ============================================
    # MILK TEA
    # ============================================
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
    
    # ============================================
    # COFFEE MIX
    # ============================================
    f = Formula(code='F-004-OLD', name='Coffee Mix', version='1.0', status='archived', created_by='system')
    db.session.add(f); db.session.flush()
    add_ings(f, [('Non-dairy creamer', 31), ('Sugar', 62), ('Magnesium stearate', 1), ('Maltdextrin', 10), ('CMC', 0.25), ('Fibre', 0.25), ('Coffee powder', 4.5)])
    
    f = Formula(code='F-004', name='Coffee Mix', version='2.0', status='approved', created_by='system')
    db.session.add(f); db.session.flush()
    add_ings(f, [('Non-dairy creamer', 31), ('Sugar', 62), ('Magnesium stearate', 1), ('Maltdextrin', 10), ('Corn starch', 5), ('Coffee powder', 4.5)])
    
    f = Formula(code='F-004S', name='Small Size Coffee Mix', version='1.0', status='archived', created_by='system')
    db.session.add(f); db.session.flush()
    add_ings(f, [('Coffee powder', 4.5), ('Azika powder', 21.7), ('Non-dairy creamer', 9.3), ('Sugar', 44), ('Malt dextrin', 10), ('Magnesium stearate', 1), ('CMC', 0.25), ('Fibre', 0.25)])
    
    # ============================================
    # MILK POWDER
    # ============================================
    f = Formula(code='F-005', name='Milk Powder', version='1.0', status='approved', created_by='system')
    db.session.add(f); db.session.flush()
    add_ings(f, [('Non-dairy creamer', 50), ('Vitamins', 0.2)])
    
    f = Formula(code='F-005S', name='Small Size Milk Powder', version='1.0', status='archived', created_by='system')
    db.session.add(f); db.session.flush()
    add_ings(f, [('Azika milk', 42.5), ('Non-dairy creamer', 7.5), ('Vitamins', 0.2)])
    
    f = Formula(code='F-005BIG', name='Big Size Milk Powder', version='1.0', status='archived', created_by='system')
    db.session.add(f); db.session.flush()
    add_ings(f, [('Non-dairy creamer', 50), ('Vitamins', 0.2)])
    
    # ============================================
    # GARRI CHOCOLATE MIX
    # ============================================
    f = Formula(code='F-006', name='Garri Chocolate Mix', version='1.0', status='approved', created_by='system')
    db.session.add(f); db.session.flush()
    add_ings(f, [('Garri', 23.1), ('Sugar', 6), ('Milk', 8), ('Cocoa powder', 1)])
    
    # ============================================
    # WHEAT FLOUR
    # ============================================
    f = Formula(code='F-007-OLD', name='Wheat Flour', version='1.0', status='archived', created_by='system')
    db.session.add(f); db.session.flush()
    add_ings(f, [('Australian wheat', 22.5), ('Russian wheat', 22.5), ('Starch', 5), ('Vitamins', 0.5)])
    
    f = Formula(code='F-007', name='Wheat Flour', version='2.0', status='approved', created_by='system')
    db.session.add(f); db.session.flush()
    add_ings(f, [('Wheat', 45), ('Starch', 5), ('Vitamins', 0.5)])
    
    # ============================================
    # RICE FLOUR
    # ============================================
    f = Formula(code='F-008', name='Rice Flour', version='1.0', status='approved', created_by='system')
    db.session.add(f); db.session.flush()
    add_ings(f, [('Rice', 1)])
    
    # ============================================
    # GINGER WITH HONEY TEA
    # ============================================
    f = Formula(code='F-009', name='Ginger with Honey Tea', version='1.0', status='approved', created_by='system')
    db.session.add(f); db.session.flush()
    add_ings(f, [('Ginger with honey premix', 1)])
    
    # ============================================
    # CUBE SEASONINGS
    # ============================================
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
    
    # ============================================
    # TOMATO PASTES
    # ============================================
    f = Formula(code='F-021', name='Ric-giko/Tomagood/Erisco Tomato Paste', version='1.0', status='approved', created_by='system')
    db.session.add(f); db.session.flush()
    add_ings(f, [('Concentrate', 480), ('Water', 1960), ('Fibre', 180), ('Sugar', 140), ('Salt', 50), ('Citric acid', 14.3), ('Potassium sorbate', 8.7), ('Caramel', 1), ('Msg', 1), ('Colour (Erythrosine)', 0.06), ('Colour (Ponceau 4R)', 0.074), ('Colour (Sunset Yellow)', 0.06), ('Colour (Allura Red)', 0.008), ('Stabilizer (CMC)', 0.067), ('Starch', 5), ('Maltdextrin', 6), ('Sodium ascorbate', 0.2)])
    
    f = Formula(code='F-022', name='Erisco Tomato Paste', version='1.0', status='approved', created_by='system')
    db.session.add(f); db.session.flush()
    add_ings(f, [('Concentrate', 720), ('Water', 1960), ('Fibre', 180), ('Sugar', 150), ('Salt', 60), ('Citric acid', 14.3), ('Potassium sorbate', 8.7), ('Caramel', 6), ('Msg', 1), ('Colour (Erythrosine)', 0.06), ('Colour (Ponceau 4R)', 0.075), ('Colour (Sunset Yellow)', 0.061), ('Colour (Allura Red)', 0.009), ('Stabilizer (CMC)', 0.067), ('Maltdextrin', 6), ('Sodium ascorbate', 2)])
    
    f = Formula(code='F-023', name='Nagiko Tomato Paste', version='1.0', status='approved', created_by='system')
    db.session.add(f); db.session.flush()
    add_ings(f, [('Tomato concentrate', 330), ('Water', 2110), ('Fibre', 200), ('Sugar', 75), ('Salt', 40), ('Citric acid', 14.3), ('Potassium sorbate', 8.7), ('Caramel', 3), ('Msg', 1), ('Colour (Erythrosine)', 0.06), ('Colour (Ponceau 4R)', 0.074), ('Colour (Sunset Yellow)', 0.06), ('Colour (Allura Red)', 0.008), ('Stabilizer (CMC)', 0.067), ('Starch', 5), ('Maltdextrin', 6)])
    
    f = Formula(code='F-024', name='Erisco Party Jollof Tomato Paste', version='1.0', status='approved', created_by='system')
    db.session.add(f); db.session.flush()
    add_ings(f, [('Concentrate', 480), ('Water', 1960), ('Chicken powder', 8), ('Chicken oil', 0.5), ('Garlic', 6), ('Ginger', 5), ('Tumeric', 5), ('Onion powder', 40), ('Fibre', 180), ('Sugar', 140), ('Salt', 50), ('Citric acid', 14.3), ('Potassium sorbate', 8.7), ('Caramel', 1), ('Msg', 25), ('Colour (Sunset Yellow)', 0.157), ('Colour (Allura Red)', 0.208), ('Stabilizer (CMC)', 0.067), ('Starch', 5), ('Maltdextrin', 6), ('Sodium erythrobate', 0.2), ('Pepper', 8), ('Palm oil', 3)])
    
    f = Formula(code='F-025', name='Erisco So Red Ketchup', version='1.0', status='approved', created_by='system')
    db.session.add(f); db.session.flush()
    add_ings(f, [('Tomato concentrate', 233), ('Fibre', 30), ('Sugar', 253), ('Salt', 30), ('Citric acid', 10), ('Potassium sorbate', 4.5), ('Onion powder', 3), ('Colour (Erythrosine)', 0.035), ('Colour (Ponceau 4R)', 0.015), ('Acetic acid', 5), ('Corn starch', 20), ('Water', 940)])
    
    # Update batch sizes
    for formula in Formula.query.all():
        total = sum(ing.quantity for ing in formula.ingredients)
        formula.batch_size = total
    
    db.session.commit()
    print(f"{Formula.query.count()} formulas seeded!")
    
if __name__ == '__main__':
    init_db()
    app.run(debug=True, host='0.0.0.0', port=5000)