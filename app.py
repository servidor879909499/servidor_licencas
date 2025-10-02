from flask import Flask, render_template, request, redirect, url_for, jsonify

app = Flask(__name__)

# Banco de dados em memória (substituir por PostgreSQL/MySQL no futuro)
licencas = [
    {"id": 1, "cliente": "Empresa A", "status": "ativo", "dias": 30},
    {"id": 2, "cliente": "Empresa B", "status": "bloqueado", "dias": 0},
]

@app.route("/")
def index():
    return redirect(url_for("painel"))

@app.route("/painel")
def painel():
    return render_template("painel.html", licencas=licencas)

@app.route("/prolongar/<int:licenca_id>", methods=["POST"])
def prolongar(licenca_id):
    for lic in licencas:
        if lic["id"] == licenca_id:
            lic["dias"] += 30
            lic["status"] = "ativo"
    return redirect(url_for("painel"))

@app.route("/bloquear/<int:licenca_id>", methods=["POST"])
def bloquear(licenca_id):
    for lic in licencas:
        if lic["id"] == licenca_id:
            lic["status"] = "bloqueado"
            lic["dias"] = 0
    return redirect(url_for("painel"))

# API para sincronizar com os clientes NV Sistema
@app.route("/api/licencas")
def api_licencas():
    return jsonify(licencas)

if __name__ == "__main__":
    app.run(debug=True)
