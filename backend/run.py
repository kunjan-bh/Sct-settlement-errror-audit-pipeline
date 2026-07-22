"""
Run with: python run.py
Starts the Flask dev server on http://localhost:5000
"""
from app import create_app
from app.extensions import db

app = create_app()

with app.app_context():
    db.create_all()  # safe to call repeatedly -- only creates missing tables

if __name__ == "__main__":
    app.run(debug=True, port=5000)
