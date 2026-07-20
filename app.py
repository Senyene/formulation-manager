from flask import Flask, render_template, redirect, url_for, request, flash
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
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

# NEW: Raw Materials table
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

# NEW: Formulas table
class Formula(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(20), unique=True, nullable=False)
    name = db.Column(db.String(200), nullable=False)
    version = db.Column(db.String(10), default='1.0')
    status = db.Column(db.String(20), default='draft')  # draft, approved, archived
    created_by = db.Column(db.String(80))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    ingredients = db.relationship('FormulaIngredient', backref='formula', lazy=True)

# NEW: Formula Ingredients (links formulas to raw materials)
class FormulaIngredient(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    formula_id = db.Column(db.Integer, db.ForeignKey('formula.id'), nullable=False)
    raw_material_id = db.Column(db.Integer, db.ForeignKey('raw_material.id'), nullable=False)
    quantity = db.Column(db.Float, nullable=False)
    unit = db.Column(db.String(20), default='kg')
    material = db.relationship('RawMaterial')

# NEW: QC Test Results table
class QCTestResult(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    formula_id = db.Column(db.Integer, db.ForeignKey('formula.id'), nullable=False)
    batch_number = db.Column(db.String(50), nullable=False)
    test_date = db.Column(db.DateTime, default=datetime.utcnow)
    tested_by = db.Column(db.String(80))
    
    # Flexible parameters - we'll store as JSON string for flexibility
    parameters = db.Column(db.Text)  # JSON string: [{"name":"Viscosity","result":"1500","unit":"cP","spec":"1400-1600","pass":true}]
    
    status = db.Column(db.String(20), default='pending')  # pass, fail, pending
    notes = db.Column(db.Text)
    
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
    
    # Pass data specific to each role
    if current_user.role == 'qc':
        formulas = Formula.query.filter_by(status='approved').all()
        recent_tests = QCTestResult.query.order_by(QCTestResult.test_date.desc()).limit(10).all()
        return render_template(template, formulas=formulas, recent_tests=recent_tests)
    
    elif current_user.role == 'rd':
        formulas = Formula.query.order_by(Formula.created_at.desc()).all()
        materials = RawMaterial.query.order_by(RawMaterial.name).all()
        # Get QC results for all formulas
        qc_feed = QCTestResult.query.order_by(QCTestResult.test_date.desc()).limit(20).all()
        return render_template(template, formulas=formulas, materials=materials, qc_feed=qc_feed)
    
    elif current_user.role == 'planner':
        # Get consumption data from QC batches
        qc_batches = QCTestResult.query.order_by(QCTestResult.test_date.desc()).all()
        return render_template(template, qc_batches=qc_batches)
    
    return render_template(template)

# NEW: QC Submit Test Result
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
    
    # Collect parameters
    param_names = request.form.getlist('param_name[]')
    param_results = request.form.getlist('param_result[]')
    param_units = request.form.getlist('param_unit[]')
    param_specs = request.form.getlist('param_spec[]')
    
    # Build parameters JSON
    parameters = []
    all_pass = True
    for i in range(len(param_names)):
        if param_names[i]:  # Only add if name is filled
            # Simple pass/fail check based on spec range
            spec_range = param_specs[i] if i < len(param_specs) else ''
            result_val = param_results[i] if i < len(param_results) else ''
            
            # Try to parse spec range like "1400-1600"
            param_pass = True
            if spec_range and result_val:
                try:
                    low, high = spec_range.split('-')
                    result_float = float(result_val)
                    param_pass = float(low) <= result_float <= float(high)
                except:
                    param_pass = True  # Can't parse, assume pass
            
            if not param_pass:
                all_pass = False
            
            parameters.append({
                'name': param_names[i],
                'result': param_results[i] if i < len(param_results) else '',
                'unit': param_units[i] if i < len(param_units) else '',
                'spec': spec_range,
                'pass': param_pass
            })
    
    import json
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
    
    # Check if code already exists
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
    
    # Check if ingredient already exists in formula
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
        
        # Seed some sample formulas and materials if empty
        if Formula.query.count() == 0:
            # Create sample raw materials
            materials = [
                RawMaterial(code='RM-001', name='Epoxy Resin A', supplier='ChemSupply Co', unit='kg', cost_per_unit=12.50, stock_level=500, created_by='system'),
                RawMaterial(code='RM-002', name='Hardener B', supplier='ChemSupply Co', unit='kg', cost_per_unit=8.75, stock_level=300, created_by='system'),
                RawMaterial(code='RM-003', name='Pigment Red', supplier='ColorTech Ltd', unit='kg', cost_per_unit=25.00, stock_level=50, created_by='system'),
                RawMaterial(code='RM-004', name='Filler Silica', supplier='MineralPro', unit='kg', cost_per_unit=3.20, stock_level=1000, created_by='system'),
            ]
            db.session.add_all(materials)
            db.session.commit()
            
            # Create sample formula
            formula = Formula(code='F-101', name='Standard Red Coating', version='1.0', status='approved', created_by='system')
            db.session.add(formula)
            db.session.commit()
            
            # Add ingredients
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