from flask import Flask, render_template, request, jsonify, session, send_file, redirect, url_for, abort, current_app
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime
import os
from sqlalchemy import inspect, text
import uuid
import json
from functools import wraps
from sqlalchemy.exc import SQLAlchemyError

app = Flask(__name__)
app.config['SECRET_KEY'] = os.urandom(24)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///forum.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  # 10 MB limit

db = SQLAlchemy(app)

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(120), nullable=False)
    role = db.Column(db.String(20), default='user')  # 'user', 'moderator', 'admin'
    posts = db.relationship('Post', backref='author', lazy=True)
    comments = db.relationship('Comment', backref='author', lazy=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

class Post(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text, nullable=True)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False)
    file_path = db.Column(db.String(255))
    file_type = db.Column(db.String(50))
    post_hash = db.Column(db.String(64), unique=True, nullable=False)
    comments = db.relationship('Comment', backref='post', cascade='all, delete-orphan', passive_deletes=True, lazy=True)

class Comment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    post_id = db.Column(db.Integer, db.ForeignKey('post.id', ondelete='CASCADE'), nullable=False)
    post_hash = db.Column(db.String(64), nullable=False)

@app.before_request
def before_request():
    if 'sqlite' in app.config['SQLALCHEMY_DATABASE_URI']:
        with db.engine.connect() as connection:
            connection.execute(text('PRAGMA foreign_keys = ON'))

def requires_role(roles):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user_id' not in session:
                return jsonify({'success': False, 'message': 'You must be logged in to perform this action'}), 403
            user = User.query.get(session['user_id'])
            if user.role not in roles:
                return jsonify({'success': False, 'message': 'You do not have permission to perform this action'}), 403
            return f(*args, **kwargs)
        return decorated_function
    return decorator

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/post/<string:post_hash>')
def post_detail(post_hash):
    try:
        post = Post.query.filter_by(post_hash=post_hash).first()
        if post is None:
            current_app.logger.error(f"No post found with hash: {post_hash}")
            abort(404, description="Post not found")
        comments = Comment.query.filter_by(post_hash=post_hash).order_by(Comment.created_at.desc()).all()
        current_user = User.query.get(session.get('user_id')) if 'user_id' in session else None
        return render_template('post_detail.html', post=post, comments=comments, current_user=current_user)
    except SQLAlchemyError as e:
        current_app.logger.error(f"Database error in post_detail: {str(e)}")
        abort(500, description="Database error occurred")
    except Exception as e:
        current_app.logger.error(f"Unexpected error in post_detail: {str(e)}")
        abort(500, description="An unexpected error occurred")

@app.route('/api/posts')
def get_posts():
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'You must be logged in to view posts'})
    posts = Post.query.order_by(Post.created_at.desc()).all()
    return jsonify([{
        'id': post.id,
        'title': post.title,
        'description': post.description,
        'content': post.content,
        'author': post.author.username,
        'date': post.created_at.strftime('%Y-%m-%d %H:%M:%S'),
        'comments_count': len(post.comments),
        'file_path': post.file_path,
        'file_type': post.file_type,
        'post_hash': post.post_hash
    } for post in posts])

@app.route('/api/comments/<string:post_hash>')
def get_comments(post_hash):
    comments = Comment.query.filter_by(post_hash=post_hash).order_by(Comment.created_at.desc()).all()
    return jsonify([{
        'id': comment.id,
        'content': comment.content,
        'author': comment.author.username,
        'date': comment.created_at.strftime('%Y-%m-%d %H:%M:%S')
    } for comment in comments])

@app.route('/login', methods=['POST'])
def login():
    username = request.form.get('username')
    password = request.form.get('password')
    user = User.query.filter_by(username=username).first()
    if user and check_password_hash(user.password, password):
        session['user_id'] = user.id
        return jsonify({'success': True})
    return jsonify({'success': False, 'message': 'Invalid credentials'})

@app.route('/logout', methods=['POST'])
def logout():
    session.pop('user_id', None)
    return jsonify({'success': True})

def load_secret_codes(filename='secret_codes.json'):
    if not os.path.exists(filename):
        raise FileNotFoundError(f"Secret codes file '{filename}' not found. Please run generate_secret_codes.py to create it.")
    
    with open(filename, 'r') as f:
        return json.load(f)

secret_codes = load_secret_codes()

@app.route('/register', methods=['POST'])
def register():
    username = request.form.get('username')
    password = request.form.get('password')
    role = request.form.get('role')
    secret_code = request.form.get('secret_code')

    existing_user = User.query.filter_by(username=username).first()
    if existing_user:
        return jsonify({'success': False, 'message': 'Username already exists'})

    if role == 'user':
        new_user = User(username=username, password=generate_password_hash(password), role=role)
        db.session.add(new_user)
        db.session.commit()
        return jsonify({'success': True})

    if role in ['moderator', 'admin']:
        if secret_code != secret_codes[role]:
            return jsonify({'success': False, 'message': 'Invalid secret code'})

    new_user = User(username=username, password=generate_password_hash(password), role=role)
    db.session.add(new_user)
    db.session.commit()
    return jsonify({'success': True})
    
@app.route('/create_post', methods=['POST'])
def create_post():
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'You must be logged in to create a post'})
    
    title = request.form.get('title')
    description = request.form.get('description', '')  # Default to empty string if not provided
    content = request.form.get('content')
    file = request.files.get('file')
    
    file_path = None
    file_type = None
    filename = None
    
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
        file.save(file_path)
        file_type = file.filename.rsplit('.', 1)[1].lower()
    
    post_hash = str(uuid.uuid4())
    
    new_post = Post(title=title, description=description, content=content, user_id=session['user_id'], file_path=filename, file_type=file_type, post_hash=post_hash)
    db.session.add(new_post)
    db.session.commit()
    return jsonify({'success': True})

@app.route('/add_comment', methods=['POST'])
def add_comment():
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'You must be logged in to add a comment'})
    
    content = request.form.get('content')
    post_hash = request.form.get('post_hash')
    
    post = Post.query.filter_by(post_hash=post_hash).first()
    if not post:
        return jsonify({'success': False, 'message': 'Post not found'})
    
    new_comment = Comment(content=content, user_id=session['user_id'], post_id=post.id, post_hash=post_hash)
    db.session.add(new_comment)
    db.session.commit()
    return redirect(url_for('post_detail', post_hash=post_hash))

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_file(os.path.join(app.config['UPLOAD_FOLDER'], filename))

def allowed_file(filename):
    ALLOWED_EXTENSIONS = {'txt', 'pdf', 'png', 'jpg', 'jpeg', 'gif'}
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/api/user')
def get_user():
    if 'user_id' in session:
        user = User.query.get(session['user_id'])
        return jsonify({
            'id': user.id,
            'username': user.username,
            'role': user.role
        })
    return jsonify(None)

@app.route('/api/user_stats')
def get_user_stats():
    if 'user_id' not in session:
        return jsonify({'message': 'You must be logged in to view user stats'})
    user = User.query.get(session['user_id'])
    post_count = Post.query.filter_by(user_id=user.id).count()
    join_date = user.created_at.strftime('%Y-%m-%d')
    return jsonify({
        'post_count': post_count,
        'join_date': join_date
    })

@app.route('/admin/users')
@requires_role(['admin'])
def admin_users():
    users = User.query.all()
    return render_template('admin_users.html', users=users)

@app.route('/admin/delete_user/<int:user_id>', methods=['POST'])
@requires_role(['admin'])
def delete_user(user_id):
    user = User.query.get_or_404(user_id)
    db.session.delete(user)
    db.session.commit()
    return jsonify({'success': True})

@app.route('/edit_post/<string:post_hash>', methods=['POST'])
@requires_role(['admin', 'moderator'])
def edit_post(post_hash):
    post = Post.query.filter_by(post_hash=post_hash).first_or_404()
    post.title = request.form.get('title')
    post.description = request.form.get('description')
    post.content = request.form.get('content')
    db.session.commit()
    return jsonify({'success': True})

@app.route('/delete_post/<string:post_hash>', methods=['POST'])
@requires_role(['admin', 'moderator'])
def delete_post(post_hash):
    post = Post.query.filter_by(post_hash=post_hash).first_or_404()
    db.session.delete(post)
    db.session.commit()
    return jsonify({'success': True})

@app.errorhandler(404)
def not_found_error(error):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    return render_template('500.html'), 500

def update_database_schema():
    inspector = inspect(db.engine)
    if not inspector.has_table('post'):
        db.create_all()
    else:
        columns = [column['name'] for column in inspector.get_columns('post')]
        if 'file_type' not in columns:
            with db.engine.connect() as conn:
                conn.execute(text('ALTER TABLE post ADD COLUMN file_type VARCHAR(50)'))
        if 'post_hash' not in columns:
            with db.engine.connect() as conn:
                conn.execute(text('ALTER TABLE post ADD COLUMN post_hash VARCHAR(64) UNIQUE'))
        columns = [column['name'] for column in inspector.get_columns('comment')]
        if 'post_hash' not in columns:
            with db.engine.connect() as conn:
                conn.execute(text('ALTER TABLE comment ADD COLUMN post_hash VARCHAR(64)'))
        columns = [column['name'] for column in inspector.get_columns('user')]
        if 'created_at' not in columns:
            with db.engine.connect() as conn:
                conn.execute(text('ALTER TABLE user ADD COLUMN created_at DATETIME DEFAULT CURRENT_TIMESTAMP'))

if __name__ == '__main__':
    with app.app_context():
        update_database_schema()
    app.run(debug=True)
