from flask import Flask, render_template, request, redirect, url_for, jsonify, flash, session
import psycopg2
from datetime import datetime, timedelta
import io
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas as rcanvas
import smtplib
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email import encoders
from apscheduler.schedulers.background import BackgroundScheduler
import os
from dotenv import load_dotenv
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

app = Flask(__name__)
app.secret_key = "NV_SECRET_2026"

# ======== CONEXÃO COM O BANCO ========

def conectar():
    return psycopg2.connect(DATABASE_URL)


# ======== CRIAÇÃO/MIGRAÇÕES LEVES ========
def criar_tabelas_essenciais():
    conn = conectar()
    cur = conn.cursor()

    # ===============================
    # CLIENTES / LICENÇAS
    # ===============================
    cur.execute("""
        CREATE TABLE IF NOT EXISTS clientes_nv (
            id SERIAL PRIMARY KEY,
            empresa TEXT,
            maquina_id TEXT UNIQUE,
            chave_licenca TEXT,
            data_inicio TIMESTAMP,
            dias INTEGER DEFAULT 0,
            status TEXT DEFAULT 'ativo',
            ultima_sync TIMESTAMP,
            email TEXT,
            telefone TEXT,
            valor_mensal NUMERIC DEFAULT 0,
            ativa BOOLEAN DEFAULT FALSE,
            referencia_pagamento TEXT,
            transacao_id TEXT,
            metodo_pagamento TEXT,
            estado_pagamento TEXT DEFAULT 'pendente',
            ultimo_pagamento TIMESTAMP
        )
    """)

    # ===============================
    # CONFIGURAÇÕES
    # ===============================
    cur.execute("""
        CREATE TABLE IF NOT EXISTS configuracoes (
            id SERIAL PRIMARY KEY,
            chave TEXT UNIQUE,
            valor TEXT
        )
    """)

    # ===============================
    # FATURAS
    # ===============================
    cur.execute("""
        CREATE TABLE IF NOT EXISTS faturas_agendadas (
            id SERIAL PRIMARY KEY,

            cliente_id INTEGER
            REFERENCES clientes_nv(id)
            ON DELETE CASCADE,

            email_cliente TEXT,
            valor NUMERIC,

            dia_emissao DATE,

            proxima_envio TIMESTAMP,

            ativo BOOLEAN DEFAULT TRUE,

            criado_em TIMESTAMP DEFAULT NOW()
        )
    """)

    conn.commit()
    conn.close()

criar_tabelas_essenciais()

# =========================
# USSD SYSTEM
# =========================

@app.route("/ussd", methods=["POST"])
def ussd():

    session_id = request.values.get("sessionId")
    phone = request.values.get("phoneNumber")
    text = request.values.get("text", "")

    # PRIMEIRA ECRÃ
    if text == "":
        return "CON NV Sistema\n1. Ver Licença\n2. Pagamento\n3. Estado"

    partes = text.split("*")
    opcao = partes[0]

    if opcao == "1":
        return ver_licenca(phone)

    elif opcao == "2":
        return menu_pagamento()

    elif opcao == "3":
        return estado_pagamento(phone)

    return "END Opção inválida"

def resposta_ussd(texto):
    return texto, 200, {"Content-Type": "text/plain"}


def menu_principal():
    return "CON NV Sistema\n1. Ver Licença\n2. Pagar\n3. Estado Pagamento"

def processar_menu(partes, phone):

    opcao = partes[0]

    if opcao == "1":
        return resposta_ussd(ver_licenca(phone))

    elif opcao == "2":
        return resposta_ussd(menu_pagamento())

    elif opcao == "3":
        return resposta_ussd(estado_pagamento(phone))

    return resposta_ussd("END Opção inválida")

def ver_licenca(phone):

    conn = conectar()
    cur = conn.cursor()

    cur.execute("""
        SELECT dias, status, valor_mensal
        FROM clientes_nv
        WHERE referencia_pagamento = %s
        LIMIT 1
    """, (phone,))

    row = cur.fetchone()
    conn.close()

    if not row:
        return "END Licença não encontrada"

    dias, status, valor = row

    return f"END Licença\nDias: {dias}\nStatus: {status}\nValor: {valor} MZN"

def estado_pagamento(phone):

    conn = conectar()
    cur = conn.cursor()

    cur.execute("""
        SELECT estado_pagamento, valor_pagamento
        FROM clientes_nv
        WHERE referencia_pagamento = %s
        LIMIT 1
    """, (phone,))

    row = cur.fetchone()
    conn.close()

    if not row:
        return "END Sem dados de pagamento"

    estado, valor = row

    return f"END Estado: {estado}\nValor: {valor} MZN"

def menu_pagamento():
    return "CON Pagamento NV\n1. Movitel\n2. Vodacom\n0. Sair"

# ======== UTILITÁRIOS DE CONFIG ========
def get_config(chave, default=None):
    conn = conectar()
    cur = conn.cursor()
    cur.execute("SELECT valor FROM configuracoes WHERE chave = %s", (chave,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else default

def set_config(chave, valor):
    conn = conectar()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO configuracoes (chave, valor) VALUES (%s, %s)
        ON CONFLICT (chave) DO UPDATE SET valor = EXCLUDED.valor
    """, (chave, valor))
    conn.commit()
    conn.close()

# ======== PAINEL PRINCIPAL ========
@app.route("/")
@app.route("/painel")
def painel():
    if not session.get("admin_logado"):
        return redirect("/login")

    conn = conectar()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, empresa, maquina_id, chave_licenca,
            data_inicio, dias, status,
            ultima_sync, email, valor_mensal
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
        clientes_final.append([
            c[0],   # id
            c[1],   # empresa
            c[2],   # maquina_id
            c[3],   # chave_licenca
            c[4],   # data_inicio
            c[5],   # dias
            c[6],   # status
            c[7],   # ultima_sync
            c[8],   # email
            c[9],   # valor_mensal
            data_fim
        ])

    return render_template("painel.html", clientes=clientes_final, title="Painel de Licenças NV Sistema")

# ======== FUNÇÕES DE LICENÇA ========
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

@app.route("/remover/<int:cliente_id>", methods=["POST"])
def remover(cliente_id):
    conn = conectar()
    cur = conn.cursor()
    cur.execute("DELETE FROM clientes_nv WHERE id = %s", (cliente_id,))
    conn.commit()
    conn.close()
    flash("Empresa removida com sucesso!", "success")
    return redirect(url_for("painel"))

# ======== CONFIGURAÇÕES ========
@app.route("/configuracoes", methods=["GET", "POST"])
def configuracoes():
    if request.method == "POST":
        set_config("empresa_nome", request.form.get("empresa_nome", "B&N SERVICOS LDA"))
        set_config("empresa_nuit", request.form.get("empresa_nuit", ""))
        set_config("empresa_email", request.form.get("empresa_email", "bnsevicoslda@gmail.com"))
        set_config("empresa_telefone", request.form.get("empresa_telefone", "+258 844 648 689; +258 879 909 499"))
        set_config("smtp_host", request.form.get("smtp_host", "smtp.gmail.com"))
        set_config("smtp_port", request.form.get("smtp_port", "587"))
        set_config("smtp_user", request.form.get("smtp_user", ""))
        set_config("smtp_pass", request.form.get("smtp_pass", ""))
        flash("Configurações salvas.", "success")
        return redirect(url_for("configuracoes"))

    configs = {
        "empresa_nome": get_config("empresa_nome", "B&N SERVICOS LDA"),
        "empresa_nuit": get_config("empresa_nuit", ""),
        "empresa_email": get_config("empresa_email", "bnsevicoslda@gmail.com"),
        "empresa_telefone": get_config("empresa_telefone", "+258 844 648 689; +258 879 909 499"),
        "smtp_host": get_config("smtp_host", "smtp.gmail.com"),
        "smtp_port": get_config("smtp_port", "587"),
        "smtp_user": get_config("smtp_user", ""),
        "smtp_pass": get_config("smtp_pass", "")
    }
    return render_template("configuracoes.html", configs=configs)

# ======== Faturas ========
@app.route("/faturas")
def faturas():
    if not session.get("admin_logado"):
        return redirect("/login")
    
    conn = conectar()
    cur = conn.cursor()
    cur.execute("""
        SELECT f.id, f.cliente_id, COALESCE(c.empresa, '') as empresa, f.email_cliente, f.valor, f.dia_emissao, f.proxima_envio, f.ativo
        FROM faturas_agendadas f
        LEFT JOIN clientes_nv c ON c.id = f.cliente_id
        ORDER BY f.proxima_envio
    """)
    rows = cur.fetchall()
    cur.execute("SELECT id, empresa, email FROM clientes_nv ORDER BY empresa")
    clientes = cur.fetchall()
    conn.close()
    return render_template("faturas.html", faturas=rows, clientes=clientes, title="Faturas")

@app.route("/faturas/agendar", methods=["POST"])
def agendar_fatura():
    cliente_id = request.form.get("cliente_id")
    valor = request.form.get("valor")
    dia_emissao = request.form.get("dia_emissao")
    email_cliente_form = request.form.get("email_cliente", None)

    if not cliente_id or not valor or not dia_emissao:
        flash("Preencha todos os campos para agendar a fatura.", "warning")
        return redirect(url_for("faturas"))

    conn = conectar()
    cur = conn.cursor()
    cur.execute("SELECT email FROM clientes_nv WHERE id = %s", (cliente_id,))
    row = cur.fetchone()
    email_cliente = row[0] if row and row[0] else email_cliente_form

    try:
        proxima = datetime.strptime(dia_emissao, "%Y-%m-%d").replace(hour=9, minute=0, second=0)
    except Exception:
        flash("Formato de data inválido. Use YYYY-MM-DD.", "warning")
        conn.close()
        return redirect(url_for("faturas"))

    cur.execute("""
        INSERT INTO faturas_agendadas (cliente_id, email_cliente, valor, dia_emissao, proxima_envio, ativo)
        VALUES (%s, %s, %s, %s, %s, TRUE)
    """, (cliente_id, email_cliente, valor, dia_emissao, proxima))
    conn.commit()
    conn.close()
    flash("Fatura agendada com sucesso.", "success")
    return redirect(url_for("faturas"))

@app.route("/faturas/cancelar/<int:fatura_id>", methods=["POST"])
def cancelar_fatura(fatura_id):
    conn = conectar()
    cur = conn.cursor()
    cur.execute("UPDATE faturas_agendadas SET ativo = FALSE WHERE id = %s", (fatura_id,))
    conn.commit()
    conn.close()
    flash("Fatura agendada cancelada.", "info")
    return redirect(url_for("faturas"))

# ======== API: licenças ========
@app.route("/api/licencas", methods=["GET", "POST"])
def api_licencas():

    conn = conectar()
    cur = conn.cursor()

    # =========================
    # GET
    # =========================
    if request.method == "GET":

        cur.execute("""
            SELECT empresa, maquina_id, chave_licenca,
                   data_inicio, dias, status, email
            FROM clientes_nv
        """)

        licencas = cur.fetchall()
        conn.close()

        lista = []

        for l in licencas:
            lista.append({
                "empresa": l[0],
                "maquina_id": l[1],
                "chave_licenca": l[2],
                "data_inicio": l[3].strftime("%Y-%m-%d %H:%M:%S") if l[3] else None,
                "dias": l[4],
                "status": l[5],
                "email": l[6]
            })

        return jsonify(lista)

    # =========================
    # POST
    # =========================
    if request.method == "POST":

        data = request.get_json(silent=True)

        if not data:
            return jsonify({"erro": "JSON inválido"}), 400

    cur.execute("""
        SELECT id FROM clientes_nv WHERE maquina_id = %s
    """, (data["maquina_id"],))

    existe = cur.fetchone()

    if existe:

        cur.execute("""
            UPDATE clientes_nv
            SET empresa=%s,
                chave_licenca=%s,
                data_inicio=%s,
                dias=%s,
                status=%s,
                email=%s,
                ultima_sync=%s
            WHERE maquina_id=%s
        """, (
            data["empresa"],
            data["chave_licenca"],
            data["data_inicio"],
            data["dias"],
            data["status"],
            data.get("email"),
            datetime.now(),
            data["maquina_id"]
        ))

    else:

        cur.execute("""
            INSERT INTO clientes_nv
            (empresa, maquina_id, chave_licenca,
             data_inicio, dias, status,
             ultima_sync, email)

            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            data["empresa"],
            data["maquina_id"],
            data["chave_licenca"],
            data["data_inicio"],
            data["dias"],
            data["status"],
            datetime.now(),
            data.get("email")
        ))

    conn.commit()
    conn.close()

    return jsonify({"ok": True})

@app.route("/licencas")
def licencas():
    if not session.get("admin_logado"):
        return redirect("/login")
    conn = conectar()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, empresa, maquina_id, chave_licenca,
               data_inicio, dias, status,
               ultima_sync, email, valor_mensal
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

        clientes_final.append([
            c[0], c[1], c[2], c[3], c[4],
            c[5], c[6], c[7], c[8], c[9],
            data_fim
        ])

    return render_template(
        "licencas.html",
        clientes=clientes_final,
        title="Licenças"
    )


@app.route("/debug_db")
def debug_db():

    conn = conectar()
    cur = conn.cursor()

    cur.execute("SELECT current_database()")
    db = cur.fetchone()[0]

    cur.execute("""
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema='public'
    """)

    tabelas = [r[0] for r in cur.fetchall()]

    conn.close()

    return {
        "database": db,
        "tabelas": tabelas
    }

@app.route("/api/licencas/<maquina_id>", methods=["GET"])
def buscar_licenca(maquina_id):

    conn = conectar()
    cur = conn.cursor()

    cur.execute("""
        SELECT empresa, maquina_id, chave_licenca,
               data_inicio, dias, status, email
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
            "status": licenca[5],
            "email": licenca[6]
        })

    return jsonify({
        "error": "Licença não encontrada"
    }), 404

# ======== API PAGAMENTOS ========

@app.route("/api/pagamento/iniciar", methods=["POST"])
def iniciar_pagamento_api():
    try:
        if request.method == "POST":

            data = request.get_json(silent=True)

            if not data:
                return jsonify({"erro": "JSON inválido"}), 400

        chave = data.get("chave_licenca")
        numero = data.get("numero")
        operadora = data.get("operadora")
        valor = data.get("valor")

        if not chave or not numero or not operadora or not valor:
            return jsonify({
                "sucesso": False,
                "mensagem": "Dados incompletos"
            }), 400

        transacao_id = f"TX-{datetime.now().strftime('%Y%m%d%H%M%S')}"

        conn = conectar()
        cur = conn.cursor()

        cur.execute("""
            UPDATE clientes_nv
            SET referencia_pagamento = %s,
                transacao_id = %s,
                status = 'pendente',
                ultima_sync = %s
            WHERE chave_licenca = %s
        """, (
            numero,
            transacao_id,
            datetime.now(),
            chave
        ))

        conn.commit()
        conn.close()

        # ==================================================
        # FUTURAMENTE:
        # AQUI ENTRA API REAL MOVITEL / MPESA
        # ==================================================

        return jsonify({
            "sucesso": True,
            "mensagem": "Pedido enviado. Confirme no telemóvel.",
            "transacao_id": transacao_id
        })

    except Exception as e:
        return jsonify({
            "sucesso": False,
            "mensagem": str(e)
        }), 500

# ======== PDF e Email ========
def gerar_pdf_fatura(empresa_info, cliente_info, valor, referencia):
    buffer = io.BytesIO()
    p = rcanvas(buffer, pagesize=A4)
    width, height = A4

    p.setFont("Helvetica-Bold", 16)
    p.drawString(40, height - 80, empresa_info.get("nome", "B&N SERVICOS LDA"))
    p.setFont("Helvetica", 10)
    p.drawString(40, height - 100, f"NUIT: {empresa_info.get('nuit', '')}")
    p.drawString(40, height - 115, f"Email: {empresa_info.get('email', '')}")
    p.drawString(40, height - 130, f"Telefone: {empresa_info.get('telefone', '')}")

    p.setFont("Helvetica-Bold", 12)
    p.drawString(40, height - 170, "Fatura de Cobrança")
    p.setFont("Helvetica", 10)
    p.drawString(40, height - 190, f"Referência: {referencia}")
    p.drawString(40, height - 205, f"Data: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    p.setFont("Helvetica-Bold", 11)
    p.drawString(40, height - 240, "Cliente:")
    p.setFont("Helvetica", 10)
    p.drawString(40, height - 255, f"Nome/Empresa: {cliente_info.get('empresa', '')}")
    p.drawString(40, height - 270, f"Email: {cliente_info.get('email', '')}")

    p.setFont("Helvetica-Bold", 11)
    p.drawString(40, height - 300, "Descrição")
    p.drawString(400, height - 300, "Valor (MZN)")
    p.setFont("Helvetica", 10)
    p.drawString(40, height - 320, "Serviço Mensal")
    p.drawString(400, height - 320, f"{valor:.2f}")

    p.setFont("Helvetica-Bold", 12)
    p.drawString(40, height - 360, f"Total: {valor:.2f} MZN")

    p.showPage()
    p.save()
    buffer.seek(0)
    return buffer.read()

# ======== API PAGAMENTOS ========

@app.route("/api/pagamento/iniciar", methods=["POST"])
def iniciar_pagamento():

    data = request.json

    maquina_id = data.get("maquina_id")
    telefone = data.get("telefone")
    metodo = data.get("metodo")
    valor = data.get("valor")

    try:

        conn = conectar()
        cur = conn.cursor()

        cur.execute("""
            UPDATE clientes_nv
            SET
                telefone=%s,
                metodo_pagamento=%s,
                estado_pagamento='processando'
            WHERE maquina_id=%s
        """, (
            telefone,
            metodo,
            maquina_id
        ))

        conn.commit()
        conn.close()

        # AQUI amanhã entra gateway real
        # mpesa/emola API

        return jsonify({
            "sucesso": True,
            "mensagem": (
                f"Pedido enviado para {telefone}.\n"
                f"Confirme o PIN no telefone."
            )
        })

    except Exception as e:

        return jsonify({
            "sucesso": False,
            "erro": str(e)
        })
    
def enviar_email_com_anexo(destinatario, assunto, corpo_html, anexo_bytes, anexo_nome):
    smtp_host = get_config("smtp_host", "smtp.gmail.com")
    smtp_port = int(get_config("smtp_port", "587"))
    smtp_user = get_config("smtp_user", "")
    smtp_pass = get_config("smtp_pass", "")

    if not smtp_user or not smtp_pass:
        app.logger.error("SMTP não configurado corretamente.")
        return False

    msg = MIMEMultipart()
    msg["From"] = smtp_user
    msg["To"] = destinatario
    msg["Subject"] = assunto
    msg.attach(MIMEText(corpo_html, "html"))

    part = MIMEBase("application", "octet-stream")
    part.set_payload(anexo_bytes)
    encoders.encode_base64(part)
    part.add_header("Content-Disposition", f'attachment; filename="{anexo_nome}"')
    msg.attach(part)

    try:
        server = smtplib.SMTP(smtp_host, smtp_port, timeout=30)
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_user, [destinatario], msg.as_string())
        server.quit()
        return True
    except Exception as e:
        app.logger.exception("Erro ao enviar e-mail: %s", e)
        return False

# ======== JOB: enviar faturas ========
def verificar_e_enviar_faturas():
    app.logger.info("Verificando faturas agendadas para envio...")
    conn = conectar()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, cliente_id, email_cliente, valor, proxima_envio
        FROM faturas_agendadas
        WHERE ativo = TRUE AND proxima_envio <= %s
        ORDER BY proxima_envio
    """, (datetime.now(),))
    rows = cur.fetchall()
    for row in rows:
        f_id, cliente_id, email_cliente, valor, proxima_envio = row
        cur.execute("SELECT empresa, email FROM clientes_nv WHERE id = %s", (cliente_id,))
        cliente = cur.fetchone()
        cliente_nome = cliente[0] if cliente else "Cliente"
        cliente_email = cliente[1] or email_cliente

        empresa_info = {
            "nome": get_config("empresa_nome", "B&N SERVICOS LDA"),
            "nuit": get_config("empresa_nuit", ""),
            "email": get_config("empresa_email", "bnsevicoslda@gmail.com"),
            "telefone": get_config("empresa_telefone", "+258 844 648 689; +258 879 909 499")
        }
        cliente_info = {"empresa": cliente_nome, "email": cliente_email}
        referencia = f"FAT-{f_id}-{proxima_envio.strftime('%Y%m%d')}"
        try:
            pdf_bytes = gerar_pdf_fatura(empresa_info, cliente_info, float(valor), referencia)
        except Exception as e:
            app.logger.exception("Erro gerando PDF: %s", e)
            continue

        corpo = f"""
            <p>Olá {cliente_nome},</p>
            <p>Segue anexo a fatura de cobrança referente ao serviço mensal. Valor: <strong>{float(valor):.2f} MZN</strong>.</p>
            <p>Atenciosamente,<br>{empresa_info['nome']}</p>
        """
        if cliente_email:
            enviado = enviar_email_com_anexo(cliente_email, f"Fatura - {empresa_info['nome']}", corpo, pdf_bytes, f"{referencia}.pdf")
            if enviado:
                app.logger.info("Fatura %s enviada para %s", f_id, cliente_email)
                proxima_nova = proxima_envio + timedelta(days=30)
                cur.execute("UPDATE faturas_agendadas SET proxima_envio = %s WHERE id = %s", (proxima_nova, f_id))
                conn.commit()
            else:
                app.logger.error("Falha ao enviar fatura %s para %s", f_id, cliente_email)
        else:
            app.logger.warning("Fatura %s sem email do cliente (id=%s)", f_id, cliente_id)
    conn.close()

# ======== Scheduler ========
scheduler = BackgroundScheduler()
scheduler.add_job(func=verificar_e_enviar_faturas, trigger="interval", seconds=60)
scheduler.start()

# ======== LOGOUT ========
@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

@app.route("/teste_pagamento")
def teste_pagamento():
    return jsonify({
        "status": "ok",
        "mensagem": "API pagamento funcionando"
    })

@app.route("/atualizar_valor/<int:cliente_id>", methods=["POST"])
def atualizar_valor(cliente_id):

    if not session.get("admin_logado"):
        return redirect("/login")

    valor = request.form.get("valor_mensal", 0)

    conn = conectar()
    cur = conn.cursor()

    cur.execute("""
        UPDATE clientes_nv
        SET valor_mensal = %s,
            ultima_sync = %s
        WHERE id = %s
    """, (
        valor,
        datetime.now(),
        cliente_id
    ))

    conn.commit()
    conn.close()

    flash("Valor mensal atualizado com sucesso!", "success")

    return redirect(url_for("painel"))

@app.route("/api/licenca/valor", methods=["GET"])
def api_valor_licenca():

    maquina_id = request.args.get("maquina_id")

    try:
        conn = conectar()
        cur = conn.cursor()

        cur.execute("""
            SELECT valor_mensal, empresa, dias, status
            FROM clientes_nv
            WHERE maquina_id = %s
            LIMIT 1
        """, (maquina_id,))

        row = cur.fetchone()
        conn.close()

        if not row:
            return jsonify({
                "sucesso": False,
                "erro": "Cliente não encontrado"
            })

        return jsonify({
            "sucesso": True,
            "valor_mensal": float(row[0] or 0),
            "empresa": row[1],
            "dias": row[2],
            "status": row[3]
        })

    except Exception as e:
        return jsonify({
            "sucesso": False,
            "erro": str(e)
        }), 500

# ======== CLIENTES ========

@app.route("/clientes")
def clientes():
    conn = conectar()
    cur = conn.cursor()

    if not session.get("admin_logado"):
        return redirect("/login")
    
    cur.execute("""
        SELECT id, empresa, maquina_id, email, status
        FROM clientes_nv
        ORDER BY empresa
    """)

    clientes = cur.fetchall()

    conn.close()

    return render_template(
        "clientes.html",
        clientes=clientes,
        title="Clientes"
    )


# ======== ATUALIZAÇÕES ========

@app.route("/atualizacoes")
def atualizacoes():
    return render_template(
        "atualizacoes.html",
        title="Atualizações"
    )

from flask import request, redirect, session, flash
from werkzeug.security import generate_password_hash

# =========================================================
# LISTAR USUÁRIOS
# =========================================================
@app.route("/usuarios")
def usuarios():

    # VERIFICA LOGIN
    if not session.get("admin_logado"):
        return redirect("/login")

    # VERIFICA TIPO
    if session.get("tipo") != "admin":
        return redirect("/painel")

    conn = conectar()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, usuario, ativo, criado_em
        FROM usuarios_admin
        ORDER BY usuario
    """)

    usuarios = cur.fetchall()

    conn.close()

    return render_template(
        "usuarios.html",
        usuarios=usuarios
    )


# =========================================================
# ADICIONAR USUÁRIO
# =========================================================
@app.route("/adicionar_usuario", methods=["POST"])
def adicionar_usuario():

    if not session.get("admin_logado"):
        return redirect("/login")

    usuario = request.form.get("usuario")
    senha = request.form.get("senha")
    status = request.form.get("status")

    # CONVERTE STATUS
    ativo = True if status == "Ativo" else False

    # CRIPTOGRAFA SENHA
    senha_hash = generate_password_hash(senha)

    conn = conectar()
    cur = conn.cursor()

    # VERIFICA DUPLICADO
    cur.execute("""
        SELECT id
        FROM usuarios_admin
        WHERE usuario = %s
    """, (usuario,))

    existe = cur.fetchone()

    if existe:

        conn.close()

        flash("Usuário já existe!", "danger")

        return redirect("/usuarios")

    # INSERT
    cur.execute("""
        INSERT INTO usuarios_admin
        (
            usuario,
            senha,
            ativo
        )
        VALUES (%s, %s, %s)
    """, (
        usuario,
        senha_hash,
        ativo
    ))

    conn.commit()
    conn.close()

    flash("Usuário adicionado com sucesso!", "success")

    return redirect("/usuarios")


# =========================================================
# ATUALIZAR USUÁRIO
# =========================================================
@app.route("/atualizar_usuario/<int:id>", methods=["POST"])
def atualizar_usuario(id):

    if not session.get("admin_logado"):
        return redirect("/login")

    usuario = request.form.get("usuario")
    status = request.form.get("status")

    ativo = True if status == "Ativo" else False

    conn = conectar()
    cur = conn.cursor()

    cur.execute("""
        UPDATE usuarios_admin
        SET
            usuario = %s,
            ativo = %s
        WHERE id = %s
    """, (
        usuario,
        ativo,
        id
    ))

    conn.commit()
    conn.close()

    flash("Usuário atualizado com sucesso!", "success")

    return redirect("/usuarios")


# =========================================================
# ELIMINAR USUÁRIO
# =========================================================
@app.route("/eliminar_usuario/<int:id>")
def eliminar_usuario(id):

    if not session.get("admin_logado"):
        return redirect("/login")

    conn = conectar()
    cur = conn.cursor()

    # NÃO PERMITE ELIMINAR A SI MESMO
    cur.execute("""
        SELECT usuario
        FROM usuarios_admin
        WHERE id = %s
    """, (id,))

    usuario = cur.fetchone()

    if usuario and usuario[0] == session.get("usuario"):

        conn.close()

        flash(
            "Você não pode eliminar seu próprio usuário!",
            "danger"
        )

        return redirect("/usuarios")

    # DELETE
    cur.execute("""
        DELETE FROM usuarios_admin
        WHERE id = %s
    """, (id,))

    conn.commit()
    conn.close()

    flash("Usuário eliminado com sucesso!", "success")

    return redirect("/usuarios")

@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        usuario = request.form.get("usuario")
        senha = request.form.get("senha")

        conn = conectar()
        cur = conn.cursor()

        # LOGIN
        cur.execute("""
            SELECT
                id,
                usuario,
                tipo,
                ativo
            FROM usuarios_admin
            WHERE usuario=%s
            AND senha=%s
            AND ativo=TRUE
        """, (usuario, senha))

        user = cur.fetchone()

        conn.close()

        # SE ENCONTROU
        if user:
            print(user)
            session["admin_logado"] = True

            session["usuario_id"] = user[0]

            session["usuario"] = user[1]

            session["tipo"] = user[2]

            return redirect("/painel")

        else:

            return """
            <script>
                alert('Usuário ou senha inválidos!');
                window.location='/login';
            </script>
            """

    return """
    <!DOCTYPE html>
    <html lang="pt">

    <head>

        <meta charset="UTF-8">

        <title>Login NV Sistema</title>

        <style>

            *{
                margin:0;
                padding:0;
                box-sizing:border-box;
                font-family:Segoe UI;
            }

            body{

                height:100vh;

                display:flex;

                justify-content:center;
                align-items:center;

                background:linear-gradient(
                    135deg,
                    #1E90FF,
                    #57848b
                );
            }

            .card{

                width:360px;

                background:white;

                border-radius:18px;

                padding:35px;

                box-shadow:0 10px 30px rgba(0,0,0,0.25);

                text-align:center;
            }

            .logo{

                font-size:55px;
                margin-bottom:10px;
            }

            h2{

                color:#1E90FF;

                margin-bottom:25px;
            }

            input{

                width:100%;

                padding:13px;

                margin-bottom:15px;

                border:1px solid #d9d9d9;

                border-radius:10px;

                font-size:15px;

                outline:none;

                transition:0.2s;
            }

            input:focus{

                border-color:#1E90FF;

                box-shadow:0 0 8px rgba(30,144,255,0.3);
            }

            button{

                width:100%;

                padding:13px;

                border:none;

                border-radius:10px;

                background:#1E90FF;

                color:white;

                font-size:16px;

                font-weight:bold;

                cursor:pointer;

                transition:0.2s;
            }

            button:hover{

                background:#1877d3;
            }

            .rodape{

                margin-top:18px;

                font-size:12px;

                color:#666;
            }

        </style>

    </head>

    <body>

        <div class="card">

            <div class="logo">
                🌀
            </div>

            <h2>NV Sistema</h2>

            <form method="POST">

                <input
                    type="text"
                    name="usuario"
                    placeholder="Usuário"
                    required
                >

                <input
                    type="password"
                    name="senha"
                    placeholder="Senha"
                    required
                >

                <button type="submit">
                    Entrar
                </button>

            </form>

            <div class="rodape">
                Painel Administrativo
            </div>

        </div>

    </body>

    </html>
    """
# ======== RUN ========
if __name__ == "__main__":
    import os

    try:
        port = int(os.environ.get("PORT", 5000))

        app.run(
            host="0.0.0.0",
            port=port,
            debug=True
        )

    finally:
        try:
            scheduler.shutdown(wait=False)
        except Exception:
            pass