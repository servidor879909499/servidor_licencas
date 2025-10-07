from flask import Flask, render_template, request, redirect, url_for, jsonify, flash
import psycopg2
from datetime import datetime, timedelta

app = Flask(__name__)
app.secret_key = "sua_chave_secreta_aqui"  # Necessário para flash

# ======== CONEXÃO COM O BANCO ========
def conectar():
    return psycopg2.connect(
        "postgresql://neondb_owner:npg_cMnJsoUp74VW@ep-misty-dawn-agy72cae-pooler.c-2.eu-central-1.aws.neon.tech/neondb?sslmode=require"
    )

# ======== CRIAR TABELA ========
def criar_tabela_clientes_nv():
    conn = conectar()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS clientes_nv (
            id SERIAL PRIMARY KEY,
            empresa TEXT,
            maquina_id TEXT UNIQUE,
            chave_licenca TEXT,
            data_inicio TIMESTAMP,
            dias INTEGER,
            status TEXT,
            ultima_sync TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

criar_tabela_clientes_nv()

# ======== PÁGINA PRINCIPAL ========
@app.route("/")
@app.route("/painel")
def painel():
    conn = conectar()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, empresa, maquina_id, chave_licenca, data_inicio, dias, status, ultima_sync
        FROM clientes_nv
        ORDER BY empresa
    """)
    clientes = cur.fetchall()
    conn.close()

    clientes_final = []
    for c in clientes:
        data_inicio = c[4]
        dias = c[5] or 0
        data_fim = data_inicio + timedelta(days=dias) if data_inicio else None
        clientes_final.append(list(c) + [data_fim])

    return render_template("painel.html", clientes=clientes_final, title="Painel de Licenças NV Sistema")

# ======== FUNÇÕES DE LICENÇA COM FEEDBACK ========
def atualizar_cliente(cliente_id, dias_delta=0, status=None, action=None):
    conn = conectar()
    cur = conn.cursor()
    if action == "prolongar":
        cur.execute("""
            UPDATE clientes_nv
            SET dias = dias + %s, status='ativo', ultima_sync=%s
            WHERE id=%s
        """, (dias_delta, datetime.now(), cliente_id))
        flash(f"Licença prolongada em {dias_delta} dias!", "success")
    elif action == "diminuir":
        cur.execute("""
            UPDATE clientes_nv
            SET dias = GREATEST(dias - %s, 0), ultima_sync=%s
            WHERE id=%s
        """, (dias_delta, datetime.now(), cliente_id))
        flash(f"Licença diminuída em {dias_delta} dias!", "warning")
    elif action == "bloquear":
        cur.execute("""
            UPDATE clientes_nv
            SET status='bloqueado', ultima_sync=%s
            WHERE id=%s
        """, (datetime.now(), cliente_id))
        flash("Licença bloqueada!", "danger")
    conn.commit()
    conn.close()

@app.route("/prolongar/<int:cliente_id>", methods=["POST"])
def prolongar(cliente_id):
    dias = int(request.form.get("dias", 0))
    if dias > 0:
        atualizar_cliente(cliente_id, dias, action="prolongar")
    return redirect(url_for("painel"))

@app.route("/diminuir/<int:cliente_id>", methods=["POST"])
def diminuir(cliente_id):
    dias = int(request.form.get("dias", 0))
    if dias > 0:
        atualizar_cliente(cliente_id, dias, action="diminuir")
    return redirect(url_for("painel"))

@app.route("/bloquear/<int:cliente_id>", methods=["POST"])
def bloquear(cliente_id):
    atualizar_cliente(cliente_id, action="bloquear")
    return redirect(url_for("painel"))

# ======== ROTAS TEMPORÁRIAS PARA MENU ========
@app.route("/faturas")
def faturas():
    return "<h1>Faturas - Em desenvolvimento</h1>"

@app.route("/licencas")
def licencas():
    return "<h1>Licenças - Em desenvolvimento</h1>"

@app.route("/clientes")
def clientes():
    return "<h1>Clientes - Em desenvolvimento</h1>"

@app.route("/configuracoes")
def configuracoes():
    return "<h1>Configurações - Em desenvolvimento</h1>"

@app.route("/atualizacoes")
def atualizacoes():
    return "<h1>Atualizações - Em desenvolvimento</h1>"

# ======== LOGOUT ========
@app.route("/logout")
def logout():
    flash("Você saiu do sistema.", "info")
    return redirect(url_for("painel"))

# ======== API REGISTRO AUTOMÁTICO ========
@app.route("/api/licencas", methods=["POST"])
def api_licencas():
    """Envia ou atualiza licença no servidor"""
    data = request.get_json()
    conn = conectar()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO clientes_nv (empresa, maquina_id, chave_licenca, data_inicio, dias, status, ultima_sync)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (maquina_id)
        DO UPDATE SET empresa=EXCLUDED.empresa, chave_licenca=EXCLUDED.chave_licenca,
                      dias=EXCLUDED.dias, status=EXCLUDED.status, ultima_sync=EXCLUDED.ultima_sync
    """, (
        data["empresa"],
        data["maquina_id"],
        data["chave_licenca"],
        datetime.strptime(data["data_inicio"], "%Y-%m-%d %H:%M:%S"),
        data.get("dias", 30),
        data.get("status", "ativo"),
        datetime.now()
    ))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})

# ======== NOVA ROTA GET PARA BUSCAR LICENÇA ========
@app.route("/api/licencas/<maquina_id>", methods=["GET"])
def buscar_licenca(maquina_id):
    """Busca licença pelo ID da máquina"""
    conn = conectar()
    cur = conn.cursor()
    cur.execute("""
        SELECT empresa, maquina_id, chave_licenca, data_inicio, dias, status
        FROM clientes_nv
        WHERE maquina_id = %s
    """, (maquina_id,))
    licenca = cur.fetchone()
    conn.close()

    if licenca:
        return jsonify({
            "empresa": licenca[0],
            "maquina_id": licenca[1],
            "chave_licenca": licenca[2],
            "data_inicio": licenca[3].strftime("%Y-%m-%d %H:%M:%S") if licenca[3] else None,
            "dias": licenca[4],
            "status": licenca[5]
        })
    return jsonify({"error": "Licença não encontrada"}), 404

if __name__ == "__main__":
    app.run(debug=True)
