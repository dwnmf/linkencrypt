from app import app, db

def create_tables():
    with app.app_context():
        db.create_all()

# Ensure tables are created before the first request
create_tables()

def application(environ, start_response):
    return app(environ, start_response)

if __name__ == "__main__":
    app.run()