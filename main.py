from flask import Flask
from project.services.webhook import scrapping_event 

def create_app():
    app = Flask(__name__)
    app.register_blueprint(scrapping_event)
    return app

app = create_app()
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True, use_reloader=True)