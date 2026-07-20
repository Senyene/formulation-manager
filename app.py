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
    status = db.Column(db.String(20), default='draft')
    created_by = db.Column(db.String(80))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
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

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

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
    
    # ============================================
    # QC ROLE
    # ============================================
    if current_user.role == 'qc':
        formulas = Formula.query.filter_by(status='approved').all()
        recent_tests = QCTestResult.query.order_by(QCTestResult.test_date.desc()).limit(10).all()
        return render_template(template, formulas=formulas, recent_tests=recent_tests)
    
    # ============================================
    # R&D ROLE
    # ============================================
    elif current_user.role == 'rd':
        formulas = Formula.query.order_by(Formula.created_at.desc()).all()
        materials = RawMaterial.query.order_by(RawMaterial.name).all()
        qc_feed = QCTestResult.query.order_by(QCTestResult.test_date.desc()).limit(20).all()
        return render_template(template, formulas=formulas, materials=materials, qc_feed=qc_feed)
    
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
            approved_formulas=approved_formulas
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
            qc_pass_rate=qc_pass_rate
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
            low_stock_materials=low_stock_materials
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
            testers=testers
        )
    
    # ============================================
    # MD (EXECUTIVE) ROLE
    # ============================================
    elif current_user.role == 'md':
        total_formulas = Formula.query.count()
        approved_formulas = Formula.query.filter_by(status='approved').count()
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
            failed_tests=len([t for t in all_qc if t.status == 'fail'])
        )
    
    # Fallback for any unmatched role
    return render_template(template)

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
    
    flash('✅ Test result submitted successfully!')
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
        flash('⚠️ Formula code already exists!')
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
    
    flash(f'✅ Formula {code} created successfully!')
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
        flash('⚠️ This material is already in the formula. Edit it instead.')
        return redirect(url_for('dashboard'))
    
    ingredient = FormulaIngredient(
        formula_id=formula_id,
        raw_material_id=material_id,
        quantity=float(quantity),
        unit=unit
    )
    db.session.add(ingredient)
    db.session.commit()
    
    flash('✅ Ingredient added to formula!')
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
    
    flash('✅ Ingredient updated!')
    return redirect(url_for('dashboard'))

@app.route('/rd/remove-ingredient/<int:ingredient_id>', methods=['POST'])
@login_required
def remove_ingredient(ingredient_id):
    if current_user.role != 'rd':
        flash('Access denied')
        return redirect(url_for('dashboard'))
    
    ingredient = FormulaIngredient.query.get_or_404(ingredient_id)
    db.session.delete(ingredient)
    db.session.commit()
    
    flash('🗑️ Ingredient removed from formula.')
    return redirect(url_for('dashboard'))

@app.route('/rd/update-formula-status/<int:formula_id>', methods=['POST'])
@login_required
def update_formula_status(formula_id):
    if current_user.role != 'rd':
        flash('Access denied')
        return redirect(url_for('dashboard'))
    
    formula = Formula.query.get_or_404(formula_id)
    new_status = request.form.get('status')
    
    if new_status in ['draft', 'approved', 'archived']:
        formula.status = new_status
        db.session.commit()
        flash(f'✅ Formula {formula.code} is now {new_status.upper()}')
    
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
        flash('⚠️ Material code already exists!')
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
    
    flash(f'✅ Material {code} created!')
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
    
    flash(f'✅ Material {material.code} updated!')
    return redirect(url_for('dashboard'))

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
        flash('⚠️ Batch number already exists!')
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
    
    flash(f'✅ Production batch {batch_number} planned!')
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
    flash(f'✅ Batch {batch.batch_number} updated!')
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
            print("✅ Database created and seeded with test users!")
        
        if Formula.query.count() == 0:
            materials = [
                RawMaterial(code='RM-001', name='Epoxy Resin A', supplier='ChemSupply Co', unit='kg', cost_per_unit=12.50, stock_level=500, created_by='system'),
                RawMaterial(code='RM-002', name='Hardener B', supplier='ChemSupply Co', unit='kg', cost_per_unit=8.75, stock_level=300, created_by='system'),
                RawMaterial(code='RM-003', name='Pigment Red', supplier='ColorTech Ltd', unit='kg', cost_per_unit=25.00, stock_level=50, created_by='system'),
                RawMaterial(code='RM-004', name='Filler Silica', supplier='MineralPro', unit='kg', cost_per_unit=3.20, stock_level=45, created_by='system'),
            ]
            db.session.add_all(materials)
            db.session.commit()
            
            formula = Formula(code='F-101', name='Standard Red Coating', version='1.0', status='approved', created_by='system')
            db.session.add(formula)
            db.session.commit()
            
            ingredients = [
                FormulaIngredient(formula_id=formula.id, raw_material_id=1, quantity=60, unit='kg'),
                FormulaIngredient(formula_id=formula.id, raw_material_id=2, quantity=30, unit='kg'),
                FormulaIngredient(formula_id=formula.id, raw_material_id=3, quantity=5, unit='kg'),
                FormulaIngredient(formula_id=formula.id, raw_material_id=4, quantity=5, unit='kg'),
            ]
            db.session.add_all(ingredients)
            db.session.commit()
            
            print("✅ Sample formulas and materials seeded!")

if __name__ == '__main__':
    init_db()
    app.run(debug=True, host='0.0.0.0', port=5000)