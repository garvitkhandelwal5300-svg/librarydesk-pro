
from flask import Flask, render_template, request, redirect, url_for, session, flash, abort
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import date, timedelta, datetime
import sqlite3, os

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "librarydesk.db")
UPLOAD = os.path.join(BASE, "uploads")
os.makedirs(UPLOAD, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret-before-production")
app.config["UPLOAD_FOLDER"] = UPLOAD

def conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c

def init_db():
    c=conn()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS owners(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      username TEXT UNIQUE NOT NULL,
      password TEXT NOT NULL,
      business_name TEXT NOT NULL,
      business_type TEXT DEFAULT 'Library',
      status TEXT DEFAULT 'active',
      demo_start TEXT,
      demo_end TEXT,
      created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS students(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      owner_id INTEGER NOT NULL,
      name TEXT NOT NULL, mobile TEXT, guardian TEXT, guardian_mobile TEXT,
      address TEXT, photo TEXT, seat INTEGER, fee REAL DEFAULT 0,
      joining TEXT, due_day INTEGER DEFAULT 5, current_paid INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS payments(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      owner_id INTEGER NOT NULL, student_id INTEGER NOT NULL,
      amount REAL NOT NULL, mode TEXT NOT NULL, paid_on TEXT NOT NULL,
      note TEXT, created_at TEXT NOT NULL
    );
    """)
    c.commit()
    if not c.execute("SELECT id FROM owners WHERE username='admin'").fetchone():
        today=date.today()
        c.execute("INSERT INTO owners(username,password,business_name,business_type,status,demo_start,demo_end,created_at) VALUES(?,?,?,?,?,?,?,?)",
                  ("admin",generate_password_hash("admin123"),"LibraryDesk Admin","Admin","active",str(today),"2099-12-31",datetime.now().isoformat()))
        for u,b,t in [
            ("library_demo1","Sharma Study Library","Library"),
            ("library_demo2","Study Point Library","Library"),
            ("pg_demo1","Royal PG","PG"),
            ("pg_demo2","Comfort PG","PG")
        ]:
            c.execute("INSERT INTO owners(username,password,business_name,business_type,status,demo_start,demo_end,created_at) VALUES(?,?,?,?,?,?,?,?)",
              (u,generate_password_hash("demo123"),b,t,"active",str(today),str(today+timedelta(days=30)),datetime.now().isoformat()))
        c.commit()
    c.close()

def current_owner():
    if "owner_id" not in session: return None
    c=conn(); o=c.execute("SELECT * FROM owners WHERE id=?", (session["owner_id"],)).fetchone(); c.close()
    return o

def demo_ok(o):
    if not o: return False, "Please log in."
    if o["status"] != "active": return False, "This account has been suspended by the administrator."
    if o["business_type"] != "Admin" and o["demo_end"] and date.fromisoformat(o["demo_end"]) < date.today():
        return False, "Your free demo has expired."
    return True, ""

@app.before_request
def guard():
    if request.endpoint in {"login","static"} or request.endpoint is None: return
    if "owner_id" not in session: return redirect(url_for("login"))
    o=current_owner()
    if not o:
        session.clear(); return redirect(url_for("login"))
    if o["business_type"] != "Admin":
        ok,msg=demo_ok(o)
        if not ok and request.endpoint not in {"expired","logout"}: return redirect(url_for("expired"))

@app.route("/", methods=["GET","POST"])
def login():
    if request.method=="POST":
        u=request.form["username"].strip()
        p=request.form["password"]
        c=conn(); o=c.execute("SELECT * FROM owners WHERE username=?", (u,)).fetchone(); c.close()
        if o and check_password_hash(o["password"],p):
            session["owner_id"]=o["id"]
            if o["business_type"]!="Admin":
                ok,msg=demo_ok(o)
                if not ok: return redirect(url_for("expired"))
            return redirect(url_for("admin") if o["business_type"]=="Admin" else url_for("dashboard"))
        flash("Invalid username or password.")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear(); return redirect(url_for("login"))

@app.route("/expired")
def expired():
    o=current_owner()
    return render_template("expired.html", owner=o)

@app.route("/dashboard")
def dashboard():
    o=current_owner()
    c=conn()
    students=c.execute("SELECT * FROM students WHERE owner_id=? ORDER BY id DESC",(o["id"],)).fetchall()
    payments=c.execute("SELECT * FROM payments WHERE owner_id=? ORDER BY id DESC",(o["id"],)).fetchall()
    c.close()
    total_seats=max(20, max([s["seat"] or 0 for s in students], default=0))
    pending=sum(s["fee"] for s in students if not s["current_paid"])
    collected=sum(p["amount"] for p in payments)
    return render_template("dashboard.html",owner=o,students=students,payments=payments,total_seats=total_seats,pending=pending,collected=collected)

@app.route("/student/add", methods=["GET","POST"])
@app.route("/student/<int:sid>/edit", methods=["GET","POST"])
def student_form(sid=None):
    o=current_owner(); c=conn()
    s=c.execute("SELECT * FROM students WHERE id=? AND owner_id=?",(sid,o["id"])).fetchone() if sid else None
    if sid and not s: abort(404)
    if request.method=="POST":
        photo=s["photo"] if s else None
        f=request.files.get("photo")
        if f and f.filename:
            name=f"{o['id']}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{secure_filename(f.filename)}"
            f.save(os.path.join(UPLOAD,name)); photo=name
        vals=(request.form["name"],request.form.get("mobile"),request.form.get("guardian"),request.form.get("guardian_mobile"),
              request.form.get("address"),photo,int(request.form["seat"]),float(request.form["fee"]),
              request.form["joining"],int(request.form["due_day"]),1 if request.form.get("current_paid")=="yes" else 0)
        if s:
            c.execute("""UPDATE students SET name=?,mobile=?,guardian=?,guardian_mobile=?,address=?,photo=?,seat=?,fee=?,joining=?,due_day=?,current_paid=? WHERE id=? AND owner_id=?""", vals+(sid,o["id"]))
        else:
            cur=c.execute("""INSERT INTO students(owner_id,name,mobile,guardian,guardian_mobile,address,photo,seat,fee,joining,due_day,current_paid) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",(o["id"],)+vals)
            sid=cur.lastrowid
            if request.form.get("current_paid")=="yes":
                c.execute("INSERT INTO payments(owner_id,student_id,amount,mode,paid_on,note,created_at) VALUES(?,?,?,?,?,?,?)",
                  (o["id"],sid,float(request.form["fee"]),request.form.get("mode","Cash"),request.form.get("paid_on") or str(date.today()),"Initial fee",datetime.now().isoformat()))
        c.commit(); c.close(); flash("Student saved successfully."); return redirect(url_for("dashboard"))
    occupied={r["seat"] for r in c.execute("SELECT seat FROM students WHERE owner_id=? AND id!=?",(o["id"],sid or -1)).fetchall()}
    c.close()
    return render_template("student_form.html",owner=o,s=s,occupied=occupied,today=str(date.today()))

@app.route("/student/<int:sid>")
def student_view(sid):
    o=current_owner(); c=conn()
    s=c.execute("SELECT * FROM students WHERE id=? AND owner_id=?",(sid,o["id"])).fetchone()
    pays=c.execute("SELECT * FROM payments WHERE owner_id=? AND student_id=? ORDER BY paid_on DESC,id DESC",(o["id"],sid)).fetchall()
    c.close()
    if not s: abort(404)
    return render_template("student.html",owner=o,s=s,pays=pays)

@app.route("/student/<int:sid>/seat", methods=["POST"])
def change_seat(sid):
    o=current_owner(); seat=int(request.form["seat"]); c=conn()
    occupied=c.execute("SELECT id FROM students WHERE owner_id=? AND seat=? AND id!=?",(o["id"],seat,sid)).fetchone()
    if occupied: flash("That seat is already occupied.")
    else:
        c.execute("UPDATE students SET seat=? WHERE id=? AND owner_id=?",(seat,sid,o["id"])); c.commit(); flash(f"Seat changed to Seat {seat}.")
    c.close(); return redirect(request.referrer or url_for("dashboard"))

@app.route("/student/<int:sid>/remove", methods=["POST"])
def remove_student(sid):
    o=current_owner(); c=conn()
    c.execute("DELETE FROM students WHERE id=? AND owner_id=?",(sid,o["id"])); c.commit(); c.close()
    flash("Student removed. Payment records remain in history."); return redirect(url_for("dashboard"))

@app.route("/student/<int:sid>/payment", methods=["POST"])
def payment(sid):
    o=current_owner(); c=conn(); s=c.execute("SELECT * FROM students WHERE id=? AND owner_id=?",(sid,o["id"])).fetchone()
    if not s: c.close(); abort(404)
    amount=float(request.form["amount"]); mode=request.form["mode"]; paid_on=request.form["paid_on"] or str(date.today())
    c.execute("INSERT INTO payments(owner_id,student_id,amount,mode,paid_on,note,created_at) VALUES(?,?,?,?,?,?,?)",
              (o["id"],sid,amount,mode,paid_on,request.form.get("note",""),datetime.now().isoformat()))
    c.execute("UPDATE students SET current_paid=1 WHERE id=?",(sid,))
    c.commit(); c.close(); flash("Payment recorded successfully."); return redirect(url_for("student_view",sid=sid))

@app.route("/student/<int:sid>/due", methods=["POST"])
def mark_due(sid):
    o=current_owner(); c=conn(); c.execute("UPDATE students SET current_paid=0 WHERE id=? AND owner_id=?",(sid,o["id"])); c.commit(); c.close()
    flash("Current fee marked as due."); return redirect(request.referrer or url_for("dashboard"))

@app.route("/history")
def history():
    o=current_owner(); c=conn()
    rows=c.execute("""SELECT p.*,s.name FROM payments p LEFT JOIN students s ON s.id=p.student_id WHERE p.owner_id=? ORDER BY p.paid_on DESC,p.id DESC""",(o["id"],)).fetchall()
    c.close(); return render_template("history.html",owner=o,rows=rows)

@app.route("/admin", methods=["GET","POST"])
def admin():
    o=current_owner()
    if o["business_type"]!="Admin": abort(403)
    c=conn()
    if request.method=="POST":
        action=request.form["action"]; oid=int(request.form["owner_id"])
        if action=="toggle":
            target=c.execute("SELECT status FROM owners WHERE id=?",(oid,)).fetchone()
            c.execute("UPDATE owners SET status=? WHERE id=?",("suspended" if target["status"]=="active" else "active",oid))
        elif action=="extend":
            days=int(request.form.get("days",30)); target=c.execute("SELECT demo_end FROM owners WHERE id=?",(oid,)).fetchone()
            base=max(date.today(),date.fromisoformat(target["demo_end"])) if target["demo_end"] else date.today()
            c.execute("UPDATE owners SET demo_end=?,status='active' WHERE id=?",(str(base+timedelta(days=days)),oid))
        elif action=="create":
            start=date.today(); end=start+timedelta(days=int(request.form.get("days",30)))
            c.execute("INSERT INTO owners(username,password,business_name,business_type,status,demo_start,demo_end,created_at) VALUES(?,?,?,?,?,?,?,?)",
              (request.form["username"],generate_password_hash(request.form["password"]),request.form["business_name"],request.form["business_type"],"active",str(start),str(end),datetime.now().isoformat()))
        c.commit()
    owners=c.execute("SELECT * FROM owners WHERE business_type!='Admin' ORDER BY id DESC").fetchall(); c.close()
    return render_template("admin.html",owner=o,owners=owners,today=date.today())

@app.route("/uploads/<name>")
def uploads(name):
    from flask import send_from_directory
    return send_from_directory(UPLOAD,name)

if __name__=="__main__":
    init_db()
    app.run(debug=True,host="0.0.0.0",port=5000)
else:
    init_db()
