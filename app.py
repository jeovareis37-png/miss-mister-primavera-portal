import os, hashlib, secrets, json
from datetime import datetime
from flask import Flask, request, render_template, redirect, url_for, session, flash, jsonify

app=Flask(__name__)
app.secret_key=os.environ.get('SECRET_KEY','troque-esta-chave-em-producao')
SYNC_TOKEN=os.environ.get('SYNC_TOKEN','')
DATABASE_URL=os.environ.get('DATABASE_URL','').strip()

if DATABASE_URL:
    import psycopg2
    import psycopg2.extras
    def conn():
        return psycopg2.connect(DATABASE_URL)
    PH='%s'
else:
    import sqlite3
    DB=os.path.join(os.path.dirname(__file__),'portal_online.db')
    def conn():
        c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; return c
    PH='?'

def q(c, sql, args=()):
    if DATABASE_URL:
        cur=c.cursor(cursor_factory=psycopg2.extras.RealDictCursor); cur.execute(sql.replace('?', '%s'),args); return cur
    return c.execute(sql,args)

def init_db():
    c=conn(); cur=c.cursor()
    if DATABASE_URL:
        cur.execute('''CREATE TABLE IF NOT EXISTS candidatos (id INTEGER PRIMARY KEY, numero INTEGER, nome TEXT, turma TEXT, categoria TEXT, ativo INTEGER, codigo TEXT UNIQUE, senha_hash TEXT, portal_ativo INTEGER, total_votos INTEGER DEFAULT 0, posicao INTEGER, atualizado_em TEXT)''')
        cur.execute('''CREATE TABLE IF NOT EXISTS carnes (id INTEGER PRIMARY KEY, candidato_id INTEGER, codigo TEXT, inicio_voto INTEGER, fim_voto INTEGER, status TEXT, votos_confirmados INTEGER DEFAULT 0)''')
        cur.execute('''CREATE TABLE IF NOT EXISTS votos (numero INTEGER PRIMARY KEY, carne_id INTEGER, candidato_id INTEGER, pago INTEGER DEFAULT 0)''')
        cur.execute('''CREATE TABLE IF NOT EXISTS historico (id INTEGER PRIMARY KEY, candidato_id INTEGER, descricao TEXT, criado_em TEXT)''')
    else:
        cur.executescript('''CREATE TABLE IF NOT EXISTS candidatos (id INTEGER PRIMARY KEY, numero INTEGER, nome TEXT, turma TEXT, categoria TEXT, ativo INTEGER, codigo TEXT UNIQUE, senha_hash TEXT, portal_ativo INTEGER, total_votos INTEGER DEFAULT 0, posicao INTEGER, atualizado_em TEXT);
CREATE TABLE IF NOT EXISTS carnes (id INTEGER PRIMARY KEY, candidato_id INTEGER, codigo TEXT, inicio_voto INTEGER, fim_voto INTEGER, status TEXT, votos_confirmados INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS votos (numero INTEGER PRIMARY KEY, carne_id INTEGER, candidato_id INTEGER, pago INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS historico (id INTEGER PRIMARY KEY, candidato_id INTEGER, descricao TEXT, criado_em TEXT);''')
    c.commit(); c.close()
init_db()

@app.get('/health')
def health(): return jsonify(ok=True, service='Portal Miss e Mister 2026')

@app.route('/', methods=['GET','POST'])
@app.route('/portal', methods=['GET','POST'])
def login():
    if request.method=='POST':
        codigo=request.form.get('codigo','').strip().upper(); senha=request.form.get('senha','').strip()
        c=conn(); r=q(c,'SELECT * FROM candidatos WHERE UPPER(codigo)=? AND ativo=1 AND portal_ativo=1',(codigo,)).fetchone(); c.close()
        if r and secrets.compare_digest(r['senha_hash'], hashlib.sha256(('miss-mister-2026|'+senha).encode()).hexdigest()):
            session['cid']=r['id']; return redirect(url_for('inicio'))
        flash('Código ou senha inválidos.')
    return render_template('login.html')

@app.get('/inicio')
def inicio():
    cid=session.get('cid')
    if not cid: return redirect(url_for('login'))
    c=conn(); cand=q(c,'SELECT * FROM candidatos WHERE id=?',(cid,)).fetchone()
    if not cand: c.close(); session.clear(); return redirect(url_for('login'))
    carnes=q(c,'SELECT * FROM carnes WHERE candidato_id=? ORDER BY id DESC',(cid,)).fetchall()
    votos_por={}
    for ca in carnes:
        votos_por[ca['id']]=q(c,'SELECT numero,pago FROM votos WHERE carne_id=? ORDER BY numero',(ca['id'],)).fetchall()
    hist=q(c,'SELECT * FROM historico WHERE candidato_id=? ORDER BY id DESC LIMIT 30',(cid,)).fetchall()
    ranking=q(c,'SELECT nome,turma,total_votos,posicao FROM candidatos WHERE ativo=1 AND categoria=? ORDER BY total_votos DESC, numero',(cand['categoria'],)).fetchall()
    c.close(); return render_template('inicio.html',c=cand,carnes=carnes,votos_por=votos_por,historico=hist,ranking=ranking)

@app.get('/sair')
def sair(): session.clear(); return redirect(url_for('login'))

@app.post('/api/sync')
def sync():
    if not SYNC_TOKEN or not secrets.compare_digest(request.headers.get('X-Sync-Token',''),SYNC_TOKEN): return jsonify(ok=False,error='unauthorized'),401
    data=request.get_json(silent=True) or {}; candidatos=data.get('candidatos',[]); carnes=data.get('carnes',[]); votos=data.get('votos',[]); hist=data.get('historico',[])
    c=conn(); cur=c.cursor()
    for table in ('historico','votos','carnes','candidatos'): cur.execute(f'DELETE FROM {table}')
    for r in candidatos:
        q(c,'INSERT INTO candidatos(id,numero,nome,turma,categoria,ativo,codigo,senha_hash,portal_ativo,total_votos,posicao,atualizado_em) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(r['id'],r['numero'],r['nome'],r.get('turma',''),r['categoria'],r.get('ativo',1),r.get('codigo'),r.get('senha_hash'),r.get('portal_ativo',0),r.get('total_votos',0),r.get('posicao'),datetime.now().isoformat(timespec='seconds')))
    for r in carnes: q(c,'INSERT INTO carnes(id,candidato_id,codigo,inicio_voto,fim_voto,status,votos_confirmados) VALUES(?,?,?,?,?,?,?)',(r['id'],r.get('candidato_id'),r['codigo'],r['inicio_voto'],r['fim_voto'],r['status'],r.get('votos_confirmados',0)))
    for r in votos: q(c,'INSERT INTO votos(numero,carne_id,candidato_id,pago) VALUES(?,?,?,?)',(r['numero'],r['carne_id'],r.get('candidato_id'),r.get('pago',0)))
    for r in hist: q(c,'INSERT INTO historico(id,candidato_id,descricao,criado_em) VALUES(?,?,?,?)',(r['id'],r.get('candidato_id'),r.get('descricao',''),r.get('criado_em','')))
    c.commit(); c.close(); return jsonify(ok=True,candidatos=len(candidatos),atualizado_em=datetime.now().isoformat(timespec='seconds'))

if __name__=='__main__': app.run(host='0.0.0.0',port=int(os.environ.get('PORT','8080')))
