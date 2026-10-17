import streamlit as st
import sqlite3
import pandas as pd
import numpy as np
import plotly.express as px
import calendar
import hashlib
import hmac
from datetime import date
from fpdf import FPDF

from pathlib import Path

LOGO_PATH = Path(__file__).parent / "assets" / "5point_logo.jpeg"

if LOGO_PATH.exists():
    st.image(str(LOGO_PATH), width=250)
else:
    st.error(f"Logo not found: {LOGO_PATH}")

# =========================================================
# FIVE POINT COLLECTION & ACCOUNTS MANAGER
# Existing collection features are retained. New features:
# 1) Monthly Cash Opening Balance
# 2) Monthly Online Opening Balance
# 3) Cash/Online expense and payment entries that update balances
# Plus refund calculation and refund-payment tracking.
# =========================================================

st.set_page_config(page_title="5Point Accounts Manager", page_icon="💰", layout="wide")
DB_NAME = "five_point_collection.db"

DENOMINATIONS = [
    ("₹500 Notes", "n500", 500), ("₹200 Notes", "n200", 200),
    ("₹100 Notes", "n100", 100), ("₹50 Notes", "n50", 50),
    ("₹20 Notes", "n20", 20), ("₹10 Notes", "n10", 10),
    ("₹10 Coins", "c10", 10), ("₹5 Coins", "c5", 5),
    ("₹2 Coins", "c2", 2), ("₹1 Coins", "c1", 1),
]

def get_connection():
    conn = sqlite3.connect(DB_NAME, timeout=30, check_same_thread=False)
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn

def initialize_database():
    # Existing tables are kept; these CREATE statements do not delete old records.
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_date TEXT NOT NULL,
                payer TEXT,
                payment_type TEXT NOT NULL,
                payment_mode TEXT,
                amount REAL NOT NULL,
                n500 INTEGER DEFAULT 0, n200 INTEGER DEFAULT 0,
                n100 INTEGER DEFAULT 0, n50 INTEGER DEFAULT 0,
                n20 INTEGER DEFAULT 0, n10 INTEGER DEFAULT 0,
                c10 INTEGER DEFAULT 0, c5 INTEGER DEFAULT 0,
                c2 INTEGER DEFAULT 0, c1 INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS monthly_balances (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                year INTEGER NOT NULL,
                month INTEGER NOT NULL,
                cash_opening REAL NOT NULL DEFAULT 0,
                online_opening REAL NOT NULL DEFAULT 0,
                UNIQUE(year, month)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_date TEXT NOT NULL,
                payee TEXT NOT NULL,
                category TEXT NOT NULL,
                payment_type TEXT NOT NULL,
                amount REAL NOT NULL,
                notes TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS refunds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_date TEXT NOT NULL,
                student TEXT NOT NULL,
                received_amount REAL NOT NULL,
                actual_fee REAL NOT NULL,
                refund_due REAL NOT NULL,
                notes TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS refund_payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                refund_id INTEGER NOT NULL,
                entry_date TEXT NOT NULL,
                payment_type TEXT NOT NULL,
                amount REAL NOT NULL,
                notes TEXT,
                FOREIGN KEY(refund_id) REFERENCES refunds(id)
            )
        """)

initialize_database()

# =========================================================
# ADMIN LOGIN — four separate sessions can use the same app.
# Passwords are stored as SHA-256 hashes, not plain text.
# Change these default passwords before using the app for real financial data.
# =========================================================
ADMIN_PASSWORD_HASH = hashlib.sha256("1234567890".encode("utf-8")).hexdigest()
ADMIN_USERS = {
    "Sambhav": ADMIN_PASSWORD_HASH,
    "Gourav": ADMIN_PASSWORD_HASH,
    "Rocky": ADMIN_PASSWORD_HASH,
    "Piku Da": ADMIN_PASSWORD_HASH,
}

if "logged_in_admin" not in st.session_state:
    st.session_state.logged_in_admin = None

if not st.session_state.logged_in_admin:
    st.title("Five Point Accounts — Admin Login")
    st.write("Sign in with your own admin account. Each admin can open a separate session.")
    with st.form("admin_login_form"):
        login_user = st.selectbox("Admin username", list(ADMIN_USERS.keys()))
        login_password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Login", use_container_width=True)
    if submitted:
        supplied_hash = hashlib.sha256(login_password.encode("utf-8")).hexdigest()
        if hmac.compare_digest(supplied_hash, ADMIN_USERS[login_user]):
            st.session_state.logged_in_admin = login_user
            st.rerun()
        else:
            st.error("Incorrect password. Please try again.")
    st.info("Default password for all four accounts: 1234567890. Change it before entering real financial data.")
    st.stop()

# Logged-in admin controls are visible on every authenticated page.
st.sidebar.success(f"Signed in as: {st.session_state.logged_in_admin}")
if st.sidebar.button("Log out", use_container_width=True):
    st.session_state.logged_in_admin = None
    st.rerun()


def money(amount):
    return f"₹{float(amount or 0):,.2f}"

def read_query(query, params=()):
    with get_connection() as conn:
        return pd.read_sql_query(query, conn, params=params)

def month_dates(year, month):
    return date(year, month, 1).isoformat(), date(year, month, calendar.monthrange(year, month)[1]).isoformat()

def get_month_data(year, month):
    start, end = month_dates(year, month)
    return read_query(
        "SELECT * FROM transactions WHERE entry_date BETWEEN ? AND ? ORDER BY entry_date DESC, id DESC",
        (start, end)
    )

def get_opening(year, month):
    df = read_query(
        "SELECT cash_opening, online_opening FROM monthly_balances WHERE year=? AND month=?",
        (year, month)
    )
    if df.empty:
        return 0.0, 0.0
    return float(df.iloc[0]["cash_opening"]), float(df.iloc[0]["online_opening"])

def get_refund_data(year, month):
    start, end = month_dates(year, month)
    return read_query("""
        SELECT r.id, r.entry_date, r.student, r.received_amount, r.actual_fee,
               r.refund_due, COALESCE(SUM(p.amount), 0) AS refund_paid, r.notes
        FROM refunds r
        LEFT JOIN refund_payments p ON p.refund_id = r.id
        WHERE r.entry_date BETWEEN ? AND ?
        GROUP BY r.id
        ORDER BY r.entry_date DESC, r.id DESC
    """, (start, end))

def get_month_accounts(year, month):
    start, end = month_dates(year, month)
    cash_opening, online_opening = get_opening(year, month)
    receipts = get_month_data(year, month)
    cash_in = float(receipts.loc[receipts.payment_type == "Cash", "amount"].sum()) if not receipts.empty else 0.0
    online_in = float(receipts.loc[receipts.payment_type == "Online", "amount"].sum()) if not receipts.empty else 0.0

    expenses = read_query(
        "SELECT * FROM expenses WHERE entry_date BETWEEN ? AND ? ORDER BY entry_date DESC, id DESC",
        (start, end)
    )
    cash_expense = float(expenses.loc[expenses.payment_type == "Cash", "amount"].sum()) if not expenses.empty else 0.0
    online_expense = float(expenses.loc[expenses.payment_type == "Online", "amount"].sum()) if not expenses.empty else 0.0

    refund_payments = read_query(
        "SELECT * FROM refund_payments WHERE entry_date BETWEEN ? AND ?",
        (start, end)
    )
    cash_refund = float(refund_payments.loc[refund_payments.payment_type == "Cash", "amount"].sum()) if not refund_payments.empty else 0.0
    online_refund = float(refund_payments.loc[refund_payments.payment_type == "Online", "amount"].sum()) if not refund_payments.empty else 0.0

    return {
        "cash_opening": cash_opening, "online_opening": online_opening,
        "cash_in": cash_in, "online_in": online_in,
        "cash_expense": cash_expense, "online_expense": online_expense,
        "cash_refund": cash_refund, "online_refund": online_refund,
        "cash_closing": cash_opening + cash_in - cash_expense - cash_refund,
        "online_closing": online_opening + online_in - online_expense - online_refund,
        "expenses_df": expenses, "refund_payments_df": refund_payments,
        "receipts_df": receipts,
    }

# Header and navigation
st.title("💰 5POINT")
st.subheader("Daily Cash & Online Collection Manager")
st.caption("Collection • Opening Balance • Expenses/Payments • Refunds • Monthly Dashboard • PDF Reports")

today = date.today()
st.sidebar.title("📌 Navigation")
page = st.sidebar.radio("Choose Section", [
    "Dashboard",
    "Data Analyst Dashboard",
    "Data Analysis Lab",
    "Data Science & Prediction",
    "Data Import & Export",
    "Monthly Opening Balance",
    "Add Cash Collection",
    "Add Online Collection",
    "Refund / Fee Calculation",
    "Add Expense / Payment",
    "Transaction History",
    "Expense & Refund History",
    "Daily PDF Report",
])
selected_month = st.sidebar.date_input("Select Report Month", value=today.replace(day=1), key="selected_month")
year, month = selected_month.year, selected_month.month
month_df = get_month_data(year, month)
accounts = get_month_accounts(year, month)

# =========================================================
# DASHBOARD
# =========================================================
if page == "Dashboard":
    st.header(f"📊 Monthly Accounts Dashboard — {calendar.month_name[month]} {year}")
    st.caption("Closing balance = Opening balance + Collection − Expenses − Refunds actually paid.")

    # Dashboard filters apply to charts and the collection table below.
    st.subheader("Dashboard Filters")
    if not month_df.empty:
        available_dates = pd.to_datetime(month_df["entry_date"], errors="coerce").dropna()
        min_day = available_dates.min().date() if not available_dates.empty else date(year, month, 1)
        max_day = available_dates.max().date() if not available_dates.empty else today
    else:
        min_day, max_day = date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])
    fc1, fc2, fc3 = st.columns([1, 1, 1])
    filter_start = fc1.date_input("From date", value=min_day, key="dashboard_from")
    filter_end = fc2.date_input("To date", value=max_day, key="dashboard_to")
    payment_filter = fc3.multiselect("Payment type", ["Cash", "Online"], default=["Cash", "Online"], key="dashboard_payment_filter")
    payer_search = st.text_input("Search student / payer name", key="dashboard_payer_search")
    filtered_month_df = month_df.copy()
    if filter_start > filter_end:
        st.warning("The start date is after the end date. Please adjust the date filters.")
        filtered_month_df = filtered_month_df.iloc[0:0]
    else:
        filtered_month_df = filtered_month_df[(filtered_month_df["entry_date"] >= filter_start.isoformat()) & (filtered_month_df["entry_date"] <= filter_end.isoformat())]
    if payment_filter:
        filtered_month_df = filtered_month_df[filtered_month_df["payment_type"].isin(payment_filter)]
    else:
        filtered_month_df = filtered_month_df.iloc[0:0]
    if payer_search.strip():
        filtered_month_df = filtered_month_df[filtered_month_df["payer"].fillna("").astype(str).str.contains(payer_search.strip(), case=False, na=False)]

    c1, c2 = st.columns(2)
    c1.metric("💵 Cash Opening Balance", money(accounts["cash_opening"]))
    c2.metric("📱 Online Opening Balance", money(accounts["online_opening"]))
    c1, c2 = st.columns(2)
    c1.metric("💵 Cash Collection", money(accounts["cash_in"]))
    c2.metric("📱 Online Collection", money(accounts["online_in"]))
    c1, c2 = st.columns(2)
    c1.metric("💸 Cash Expenses / Payments", money(accounts["cash_expense"]))
    c2.metric("💳 Online Expenses / Payments", money(accounts["online_expense"]))
    c1, c2 = st.columns(2)
    c1.metric("↩️ Cash Refunds Paid", money(accounts["cash_refund"]))
    c2.metric("↩️ Online Refunds Paid", money(accounts["online_refund"]))
    st.divider()
    c1, c2, c3 = st.columns(3)
    c1.metric("💵 Closing Cash Balance", money(accounts["cash_closing"]))
    c2.metric("📱 Closing Online Balance", money(accounts["online_closing"]))
    c3.metric("💰 Total Remaining Balance", money(accounts["cash_closing"] + accounts["online_closing"]))

    refund_df = get_refund_data(year, month)
    pending = float((refund_df["refund_due"] - refund_df["refund_paid"]).clip(lower=0).sum()) if not refund_df.empty else 0.0
    st.metric("⏳ Refund Amount Still Pending", money(pending))
    st.divider()

    st.subheader("📈 Daily Collection Chart")
    if not filtered_month_df.empty:
        st.subheader("Daily Collection Chart")
        chart_df = filtered_month_df.groupby(["entry_date", "payment_type"], as_index=False)["amount"].sum()
        st.plotly_chart(px.bar(chart_df, x="entry_date", y="amount", color="payment_type", barmode="group", markers=True, title="Daily Cash vs Online Collection"), use_container_width=True)
        trend_df = chart_df.copy()
        st.plotly_chart(px.line(trend_df, x="entry_date", y="amount", color="payment_type", markers=True, title="Collection Trend by Date"), use_container_width=True)
        st.subheader(f"Filtered Collection Entries ({len(filtered_month_df):,})")
        st.dataframe(filtered_month_df[["id", "entry_date", "payer", "payment_type", "payment_mode", "amount"]],
                     use_container_width=True, hide_index=True)
        st.download_button("⬇️ Download Filtered Collection CSV", filtered_month_df.to_csv(index=False).encode("utf-8-sig"),
                           file_name=f"FivePoint_Collections_Filtered_{year}_{month:02d}.csv", mime="text/csv")
    else:
        st.info("No collection entries match the selected dashboard filters for this month.")

    st.subheader("Expense / Teacher Payment Entries")
    if not accounts["expenses_df"].empty:
        st.dataframe(accounts["expenses_df"], use_container_width=True, hide_index=True)
    else:
        st.info("No expense entries are available for this month.")

    st.subheader("Refund Summary")
    if not refund_df.empty:
        refund_df["pending_refund"] = (refund_df["refund_due"] - refund_df["refund_paid"]).clip(lower=0)
        st.dataframe(refund_df, use_container_width=True, hide_index=True)
    else:
        st.info("No refund entries are available for this month.")

# =========================================================
# 1 & 2. MONTHLY OPENING BALANCE OPTIONS
# =========================================================
elif page == "Monthly Opening Balance":
    st.header("🏦 Monthly Opening Balance")
    st.write(f"Opening balances for **{calendar.month_name[month]} {year}**")
    st.info("Enter the cash on hand and the online balance in your bank/UPI account as of the first day of the month. Saving again for the same month will update the opening balance.")
    cash_old, online_old = get_opening(year, month)
    with st.form("opening_balance_form"):
        cash_open = st.number_input("💵 Cash Opening Balance (₹)", min_value=0.0, value=float(cash_old), step=500.0)
        online_open = st.number_input("📱 Online Opening Balance (₹)", min_value=0.0, value=float(online_old), step=500.0)
        save_open = st.form_submit_button("💾 Save Monthly Opening Balance", use_container_width=True)
    if save_open:
        with get_connection() as conn:
            conn.execute("""
                INSERT INTO monthly_balances (year, month, cash_opening, online_opening)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(year, month) DO UPDATE SET
                    cash_opening=excluded.cash_opening,
                    online_opening=excluded.online_opening
            """, (year, month, cash_open, online_open))
        st.success("Cash and Online opening balances saved.")
        st.rerun()
    st.subheader("Current Month Balance")
    st.write(f"Cash closing balance: **{money(accounts['cash_closing'])}**")
    st.write(f"Online closing balance: **{money(accounts['online_closing'])}**")
    st.caption("For a new month, enter its opening balance based on the previous month’s closing balance.")

# =========================================================
# CASH COLLECTION
# =========================================================
elif page == "Add Cash Collection":
    st.header("💵 Add Daily Cash Collection")
    st.write("Enter the number of notes and coins received for each cash collection.")
    with st.form("cash_form"):
        cash_date = st.date_input("Collection Date", value=today)
        payer = st.text_input("Student / Payer Name (Optional)")
        st.subheader("💴 Count Notes & Coins")
        cols = st.columns(3)
        values = {}
        for i, (label, key, denomination) in enumerate(DENOMINATIONS):
            with cols[i % 3]:
                values[key] = st.number_input(label, min_value=0, step=1, key=f"cash_{key}")
        cash_total = sum(values[key] * denomination for _, key, denomination in DENOMINATIONS)
        st.info(f"Calculated Cash: {money(cash_total)}")
        save_cash = st.form_submit_button("💾 Save Cash Collection", use_container_width=True)
    if save_cash:
        if cash_total <= 0:
            st.error("Enter the quantity of at least one note or coin denomination.")
        else:
            with get_connection() as conn:
                conn.execute("""
                    INSERT INTO transactions (
                        entry_date, payer, payment_type, payment_mode, amount,
                        n500, n200, n100, n50, n20, n10, c10, c5, c2, c1
                    ) VALUES (?, ?, 'Cash', 'Cash', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (cash_date.isoformat(), payer, cash_total,
                      values["n500"], values["n200"], values["n100"], values["n50"],
                      values["n20"], values["n10"], values["c10"], values["c5"],
                      values["c2"], values["c1"]))
            st.success(f"Cash collection saved: {money(cash_total)}")
            st.rerun()

# =========================================================
# ONLINE COLLECTION
# =========================================================
elif page == "Add Online Collection":
    st.header("📱 Add Online Collection")
    with st.form("online_form"):
        online_date = st.date_input("Collection Date", value=today)
        payer = st.text_input("Student / Payer Name (Optional)")
        payment_mode = st.selectbox("Payment Method", ["UPI", "Google Pay", "PhonePe", "Bank Transfer", "Other"])
        amount = st.number_input("Amount Received (₹)", min_value=0.0, value=0.0, step=100.0)
        save_online = st.form_submit_button("💾 Save Online Collection", use_container_width=True)
    if save_online:
        if amount <= 0:
            st.error("Please enter a valid online amount.")
        else:
            with get_connection() as conn:
                conn.execute("""
                    INSERT INTO transactions (entry_date, payer, payment_type, payment_mode, amount)
                    VALUES (?, ?, 'Online', ?, ?)
                """, (online_date.isoformat(), payer, payment_mode, amount))
            st.success(f"Online collection saved: {money(amount)}")
            st.rerun()

# =========================================================
# REFUND CALCULATION & PAYMENTS
# =========================================================
elif page == "Refund / Fee Calculation":
    st.header("↩️ Refund / Fee Calculation")
    st.write("Enter the amount received and the actual fee to calculate the refund due, amount paid, and pending balance. The refund payment will be deducted from the selected Cash or Online balance.")
    with st.form("refund_form"):
        refund_date = st.date_input("Refund Calculation Date", value=today)
        student = st.text_input("Student Name")
        received = st.number_input("Amount Received (₹)", min_value=0.0, value=1500.0, step=50.0)
        actual_fee = st.number_input("Actual Monthly Fee (₹)", min_value=0.0, value=1350.0, step=50.0)
        notes = st.text_input("Notes (Optional)")
        due = max(received - actual_fee, 0.0)
        st.metric("Total Refund Due", money(due))
        initial_paid = st.number_input("Refund Paid Now (₹)", min_value=0.0, max_value=float(due), value=0.0, step=10.0)
        refund_mode = st.selectbox("Refund paid from", ["Cash", "Online"])
        save_refund = st.form_submit_button("💾 Save Refund Calculation", use_container_width=True)
    if save_refund:
        if not student.strip():
            st.error("Please enter the student name.")
        elif received < actual_fee:
            st.error("The amount received is less than the actual fee. There may be an outstanding fee instead of a refund.")
        else:
            with get_connection() as conn:
                cur = conn.execute("""
                    INSERT INTO refunds (entry_date, student, received_amount, actual_fee, refund_due, notes)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (refund_date.isoformat(), student.strip(), received, actual_fee, due, notes))
                refund_id = cur.lastrowid
                if initial_paid > 0:
                    conn.execute("""
                        INSERT INTO refund_payments (refund_id, entry_date, payment_type, amount, notes)
                        VALUES (?, ?, ?, ?, ?)
                    """, (refund_id, refund_date.isoformat(), refund_mode, initial_paid, "Initial refund payment"))
            st.success(f"Saved. Refund: {money(due)} | Paid: {money(initial_paid)} | Pending: {money(due-initial_paid)}")
            st.rerun()

    st.divider()
    st.subheader("Pay a Pending Refund")
    refund_df = get_refund_data(year, month)
    
    # DELETE REFUND / FEE CALCULATION
    st.divider()
    st.subheader("Delete Refund / Fee Calculation")

    delete_df = get_refund_data(year, month)

    if delete_df.empty:
        st.info("No refund calculations available to delete.")
    else:
        delete_df["pending_refund"] = (
            delete_df["refund_due"] - delete_df["refund_paid"]
        ).clip(lower=0)

        st.dataframe(
            delete_df[
                ["id", "entry_date", "student", "received_amount",
                 "actual_fee", "refund_due", "refund_paid"]
            ],
            use_container_width=True,
            hide_index=True
        )

        refund_options = {
            int(row["id"]): (
                f'ID #{int(row["id"])} - {row["student"]} - '
                f'Refund ₹{row["refund_due"]:.2f}'
            )
            for _, row in delete_df.iterrows()
        }

        with st.form("delete_refund_form"):
            refund_to_delete = st.selectbox(
                "Select refund calculation to delete",
                options=list(refund_options.keys()),
                format_func=lambda x: refund_options[x]
            )

            confirm_delete = st.checkbox(
                "I confirm that I want to delete this refund calculation "
                "and its refund payment records."
            )

            delete_button = st.form_submit_button(
                "🗑️ Delete Refund Calculation",
                use_container_width=True
            )

        if delete_button:
            if not confirm_delete:
                st.warning("Please confirm before deleting.")
            else:
                with get_connection() as conn:
                    conn.execute(
                        "DELETE FROM refund_payments WHERE refund_id = ?",
                        (int(refund_to_delete),)
                    )
                    conn.execute(
                        "DELETE FROM refunds WHERE id = ?",
                        (int(refund_to_delete),)
                    )

                st.success("Refund calculation and its payment records deleted.")
                st.rerun()
    if refund_df.empty:
        st.info("No refund calculations are available for this month.")
    else:
        refund_df["pending_refund"] = (refund_df["refund_due"] - refund_df["refund_paid"]).clip(lower=0)
        st.dataframe(refund_df[["id", "entry_date", "student", "received_amount", "actual_fee", "refund_due", "refund_paid", "pending_refund"]],
                     use_container_width=True, hide_index=True)
        pending_df = refund_df[refund_df["pending_refund"] > 0].copy()
        if not pending_df.empty:
            labels = {int(row["id"]): f'#{int(row["id"])} — {row["student"]} — pending {money(row["pending_refund"])}'
                      for _, row in pending_df.iterrows()}
            with st.form("pay_pending_refund_form"):
                selected_id = st.selectbox("Select Student Refund", list(labels.keys()), format_func=lambda x: labels[x])
                selected_pending = float(pending_df.loc[pending_df["id"] == selected_id, "pending_refund"].iloc[0])
                pay_date = st.date_input("Refund Payment Date", value=today)
                pay_amount = st.number_input("Amount Returning Now (₹)", min_value=0.0, max_value=selected_pending,
                                             value=selected_pending, step=10.0)
                pay_mode = st.selectbox("Pay from", ["Cash", "Online"])
                pay_notes = st.text_input("Payment Note (Optional)")
                save_pay = st.form_submit_button("💾 Record Refund Payment", use_container_width=True)
            if save_pay:
                if pay_amount <= 0 or pay_amount > selected_pending:
                    st.error("Please enter a valid refund payment amount.")
                else:
                    with get_connection() as conn:
                        conn.execute("""
                            INSERT INTO refund_payments (refund_id, entry_date, payment_type, amount, notes)
                            VALUES (?, ?, ?, ?, ?)
                        """, (int(selected_id), pay_date.isoformat(), pay_mode, pay_amount, pay_notes))
                    st.success(f"Refund payment recorded: {money(pay_amount)}")
                    st.rerun()
        else:
            st.success("All refunds for this month have been paid.")

# =========================================================
# 3. EXPENSE / PAYMENT ENTRY
# =========================================================
elif page == "Add Expense / Payment":
    st.header("💸 Add Expense / Payment")
    st.write("Record teacher salaries, staff payments, rent, or other expenses. The selected Cash or Online balance will be reduced.")
    with st.form("expense_form"):
        expense_date = st.date_input("Payment Date", value=today)
        payee = st.text_input("Paid To (Teacher / Person / Vendor)")
        category = st.selectbox("Expense Category", [
            "Teacher Salary", "Staff Payment", "Rent", "Electricity / Internet",
            "Study Materials", "Food / Refreshments", "Repair / Maintenance", "Other"
        ])
        payment_type = st.selectbox("Paid From", ["Cash", "Online"])
        amount = st.number_input("Amount Paid (₹)", min_value=0.0, value=0.0, step=100.0)
        notes = st.text_area("Purpose / Details (Optional)")
        save_expense = st.form_submit_button("💾 Save Expense / Payment", use_container_width=True)
    if save_expense:
        if not payee.strip():
            st.error("Please enter the name of the person or vendor who was paid.")
        elif amount <= 0:
            st.error("The amount must be greater than zero.")
        else:
            with get_connection() as conn:
                conn.execute("""
                    INSERT INTO expenses (entry_date, payee, category, payment_type, amount, notes)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (expense_date.isoformat(), payee.strip(), category, payment_type, amount, notes))
            st.success(f"Expense saved: {money(amount)} from {payment_type}")
            st.rerun()

# =========================================================
# COLLECTION HISTORY
# =========================================================
elif page == "Transaction History":
    st.header("🧾 Collection Transaction History")
    if month_df.empty:
        st.info("No collection entries are available for this month.")
    else:
        st.dataframe(month_df[["id", "entry_date", "payer", "payment_type", "payment_mode", "amount"]],
                     use_container_width=True, hide_index=True)
        st.download_button("⬇️ Download Collection CSV", month_df.to_csv(index=False).encode("utf-8-sig"),
                           file_name=f"FivePoint_Collections_{year}_{month:02d}.csv", mime="text/csv")
        st.subheader("Delete Incorrect Collection Entry")
        transaction_id = st.selectbox("Select Transaction ID", month_df["id"].astype(int).tolist())
        confirm = st.checkbox("I confirm that I want to delete this collection entry.")
        if st.button("🗑️ Delete Collection Entry"):
            if confirm:
                with get_connection() as conn:
                    conn.execute("DELETE FROM transactions WHERE id=?", (int(transaction_id),))
                st.success("Collection entry deleted.")
                st.rerun()
            else:
                st.error("Please confirm the deletion first.")

# =========================================================
# EXPENSE & REFUND HISTORY
# =========================================================
elif page == "Expense & Refund History":
    st.header("📚 Expense & Refund History")
    start, end = month_dates(year, month)
    expense_df = read_query(
        "SELECT * FROM expenses WHERE entry_date BETWEEN ? AND ? ORDER BY entry_date DESC, id DESC",
        (start, end)
    )
    refund_df = get_refund_data(year, month)
    st.subheader("Expenses / Teacher Payments")
    if expense_df.empty:
        st.info("No expense or payment entries are available for this month.")
    else:
        st.dataframe(expense_df, use_container_width=True, hide_index=True)
        st.download_button("⬇️ Download Expense CSV", expense_df.to_csv(index=False).encode("utf-8-sig"),
                           file_name=f"FivePoint_Expenses_{year}_{month:02d}.csv", mime="text/csv")
        st.markdown("**Delete an incorrect expense or teacher payment:**")
        expense_id = st.selectbox(
            "Select Expense ID",
            expense_df["id"].astype(int).tolist(),
            format_func=lambda x: f"ID {x} — {expense_df.loc[expense_df['id'] == x, 'payee'].iloc[0]} — {money(float(expense_df.loc[expense_df['id'] == x, 'amount'].iloc[0]))}",
            key="delete_expense_id"
        )
        confirm_expense = st.checkbox("I confirm that I want to delete this expense/payment.", key="confirm_delete_expense")
        if st.button("🗑️ Delete Expense / Payment", key="delete_expense_button"):
            if confirm_expense:
                with get_connection() as conn:
                    conn.execute("DELETE FROM expenses WHERE id=?", (int(expense_id),))
                st.success("Expense/payment entry deleted. Balance will be recalculated.")
                st.rerun()
            else:
                st.error("Please confirm the deletion first.")

    st.subheader("Student Refunds")
    if refund_df.empty:
        st.info("No refund entries are available for this month.")
    else:
        refund_df["pending_refund"] = (refund_df["refund_due"] - refund_df["refund_paid"]).clip(lower=0)
        st.dataframe(refund_df, use_container_width=True, hide_index=True)
        st.markdown("**Delete an incorrect refund entry:**")
        refund_id = st.selectbox(
            "Select Refund ID",
            refund_df["id"].astype(int).tolist(),
            format_func=lambda x: f"ID {x} — {refund_df.loc[refund_df['id'] == x, 'student'].iloc[0]} — Due {money(float(refund_df.loc[refund_df['id'] == x, 'refund_due'].iloc[0]))}",
            key="delete_refund_id"
        )
        confirm_refund = st.checkbox("I confirm that I want to delete this refund and its related payment records.", key="confirm_delete_refund")
        if st.button("🗑️ Delete Refund Entry", key="delete_refund_button"):
            if confirm_refund:
                with get_connection() as conn:
                    conn.execute("DELETE FROM refund_payments WHERE refund_id=?", (int(refund_id),))
                    conn.execute("DELETE FROM refunds WHERE id=?", (int(refund_id),))
                st.success("Refund entry and its payment records deleted. Balance will be recalculated.")
                st.rerun()
            else:
                st.error("Please confirm the deletion first.")

# =========================================================
# DATA ANALYST DASHBOARD
# =========================================================
elif page == "Data Analyst Dashboard":
    st.header("📊 Data Analyst Dashboard")
    st.caption(f"Financial performance analysis for {calendar.month_name[month]} {year}")
    start, end = month_dates(year, month)
    raw_collections = read_query("SELECT * FROM transactions WHERE entry_date BETWEEN ? AND ? ORDER BY entry_date", (start, end))
    expense_df = read_query("SELECT * FROM expenses WHERE entry_date BETWEEN ? AND ? ORDER BY entry_date", (start, end))
    st.subheader("Filters")
    if not raw_collections.empty:
        dts = pd.to_datetime(raw_collections["entry_date"], errors="coerce").dropna()
        default_from = dts.min().date() if not dts.empty else date(year, month, 1)
        default_to = dts.max().date() if not dts.empty else date(year, month, calendar.monthrange(year, month)[1])
    else:
        default_from, default_to = date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])
    f1, f2, f3 = st.columns(3)
    analyst_from = f1.date_input("From date", value=default_from, key="analyst_from")
    analyst_to = f2.date_input("To date", value=default_to, key="analyst_to")
    analyst_modes = f3.multiselect("Payment type", ["Cash", "Online"], default=["Cash", "Online"], key="analyst_modes")
    payer_q = st.text_input("Search payer / student", key="analyst_payer_search")
    filtered = raw_collections.copy()
    if analyst_from <= analyst_to:
        filtered = filtered[(filtered["entry_date"] >= analyst_from.isoformat()) & (filtered["entry_date"] <= analyst_to.isoformat())]
    else:
        st.warning("From date must be on or before To date.")
        filtered = filtered.iloc[0:0]
    filtered = filtered[filtered["payment_type"].isin(analyst_modes)] if analyst_modes else filtered.iloc[0:0]
    if payer_q.strip():
        filtered = filtered[filtered["payer"].fillna("").astype(str).str.contains(payer_q.strip(), case=False, na=False)]
    total_collection = float(filtered["amount"].sum()) if not filtered.empty else 0.0
    total_expenses = float(expense_df["amount"].sum()) if not expense_df.empty else 0.0
    refund_df = get_refund_data(year, month)
    total_refunds = float(refund_df["refund_paid"].sum()) if not refund_df.empty else 0.0
    cash_total = float(filtered.loc[filtered.payment_type == "Cash", "amount"].sum()) if not filtered.empty else 0.0
    online_total = float(filtered.loc[filtered.payment_type == "Online", "amount"].sum()) if not filtered.empty else 0.0
    net_movement = total_collection - total_expenses - total_refunds
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Filtered Collection", money(total_collection))
    c2.metric("Monthly Expenses", money(total_expenses))
    c3.metric("Refunds Paid This Month", money(total_refunds))
    c4.metric("Collection Less Expenses & Refunds", money(net_movement))
    c1, c2 = st.columns(2)
    c1.metric("Cash Collection (filtered)", money(cash_total))
    c2.metric("Online Collection (filtered)", money(online_total))
    if not filtered.empty:
        st.subheader("Cash vs Online Collection")
        payment_mix = pd.DataFrame({"Payment Method": ["Cash", "Online"], "Amount": [cash_total, online_total]})
        st.plotly_chart(px.pie(payment_mix, names="Payment Method", values="Amount", hole=0.45, title="Collection Mix"), use_container_width=True)
        daily = filtered.groupby(["entry_date", "payment_type"], as_index=False)["amount"].sum()
        st.plotly_chart(px.bar(daily, x="entry_date", y="amount", color="payment_type", barmode="group", title="Daily Collection by Payment Type"), use_container_width=True)
        st.plotly_chart(px.line(daily, x="entry_date", y="amount", color="payment_type", markers=True, title="Daily Collection Trend"), use_container_width=True)
        st.subheader("Collection Summary")
        summary = filtered.groupby("payment_type", as_index=False).agg(Total_Collection=("amount", "sum"), Transaction_Count=("id", "count"))
        st.dataframe(summary, use_container_width=True, hide_index=True)
        st.subheader("Filtered Collection Records")
        st.dataframe(filtered[["id", "entry_date", "payer", "payment_type", "payment_mode", "amount"]], use_container_width=True, hide_index=True)
    else:
        st.info("No collection records match these filters. Try another date range or payment type.")
    if not expense_df.empty:
        st.subheader("Expense Breakdown")
        exp_summary = expense_df.groupby("category", as_index=False)["amount"].sum().sort_values("amount", ascending=False)
        st.plotly_chart(px.bar(exp_summary, x="category", y="amount", title="Expenses by Category"), use_container_width=True)
        st.plotly_chart(px.pie(exp_summary, names="category", values="amount", hole=0.4, title="Expense Share by Category"), use_container_width=True)
    st.download_button("Download Filtered Analytics CSV", filtered.to_csv(index=False).encode("utf-8-sig"), file_name=f"FivePoint_Analytics_{year}_{month:02d}.csv", mime="text/csv")

# =========================================================
# DATA ANALYSIS LAB
# =========================================================
elif page == "Data Analysis Lab":
    st.header("🧪 Data Analysis Lab")
    st.write("Upload a CSV or Excel dataset to explore, clean, summarize, and export it.")
    uploaded = st.file_uploader("Upload CSV or Excel File", type=["csv", "xlsx", "xls"])
    if uploaded is not None:
        try:
            if uploaded.name.lower().endswith(".csv"):
                data = pd.read_csv(uploaded)
            else:
                data = pd.read_excel(uploaded)
            st.subheader("Dataset Preview")
            st.write(f"Rows: {len(data):,} | Columns: {len(data.columns):,}")
            st.dataframe(data.head(100), use_container_width=True, hide_index=True)
            c1, c2, c3 = st.columns(3)
            c1.metric("Rows", f"{len(data):,}")
            c2.metric("Columns", f"{len(data.columns):,}")
            c3.metric("Duplicate Rows", f"{int(data.duplicated().sum()):,}")
            quality = pd.DataFrame({"Column": data.columns, "Data Type": [str(x) for x in data.dtypes], "Missing Values": data.isna().sum().values, "Missing %": (data.isna().mean().values * 100).round(2)})
            st.subheader("Data Quality Report")
            st.dataframe(quality, use_container_width=True, hide_index=True)
            numeric_cols = data.select_dtypes(include="number").columns.tolist()
            if numeric_cols:
                chosen_numeric = st.selectbox("Choose Numeric Column for Analysis", numeric_cols)
                st.subheader("Summary Statistics")
                st.dataframe(data[chosen_numeric].describe().to_frame("Value"), use_container_width=True)
                if data[chosen_numeric].notna().any():
                    st.plotly_chart(px.histogram(data, x=chosen_numeric, title=f"Distribution of {chosen_numeric}"), use_container_width=True)
            selected_columns = st.multiselect("Select Columns to Display", data.columns.tolist(), default=data.columns.tolist())
            filtered = data[selected_columns].copy()
            text_cols = filtered.select_dtypes(include=["object", "string"]).columns.tolist()
            if text_cols:
                filter_col = st.selectbox("Optional Text Filter Column", ["None"] + text_cols)
                if filter_col != "None":
                    options = filtered[filter_col].dropna().astype(str).unique().tolist()
                    chosen_values = st.multiselect("Keep Values", options)
                    if chosen_values:
                        filtered = filtered[filtered[filter_col].astype(str).isin(chosen_values)]
            remove_duplicates = st.checkbox("Remove Duplicate Rows", value=False)
            fill_missing = st.checkbox("Fill Missing Numeric Values with Column Mean", value=False)
            if remove_duplicates:
                filtered = filtered.drop_duplicates()
            if fill_missing:
                for col in filtered.select_dtypes(include="number").columns:
                    filtered[col] = filtered[col].fillna(filtered[col].mean())
            st.subheader("Processed Dataset")
            st.dataframe(filtered.head(500), use_container_width=True, hide_index=True)
            st.download_button("Download Cleaned CSV", filtered.to_csv(index=False).encode("utf-8-sig"), file_name="cleaned_dataset.csv", mime="text/csv")
            st.caption("Data cleaning changes apply to the uploaded copy only. They do not modify your financial database.")
        except Exception as exc:
            st.error(f"Unable to read this file: {exc}")

# =========================================================
# DATA SCIENCE & PREDICTION
# =========================================================
elif page == "Data Science & Prediction":
    st.header("🤖 Data Science & Collection Forecasting")
    st.write("Explore historical collection patterns and generate a simple baseline forecast from recorded transactions.")
    all_transactions = read_query("SELECT entry_date, payment_type, amount FROM transactions ORDER BY entry_date")
    if all_transactions.empty:
        st.warning("There is not enough transaction history yet. Add real collection records first.")
    else:
        all_transactions["entry_date"] = pd.to_datetime(all_transactions["entry_date"], errors="coerce")
        all_transactions = all_transactions.dropna(subset=["entry_date"])
        all_transactions["month_start"] = all_transactions["entry_date"].dt.to_period("M").dt.to_timestamp()
        monthly = all_transactions.groupby("month_start", as_index=False)["amount"].sum().sort_values("month_start")
        st.subheader("Historical Monthly Collection")
        st.plotly_chart(px.line(monthly, x="month_start", y="amount", markers=True, title="Recorded Monthly Collection"), use_container_width=True)
        if len(monthly) >= 3:
            monthly["month_index"] = np.arange(len(monthly), dtype=float)
            x = monthly["month_index"].to_numpy()
            y = monthly["amount"].to_numpy(dtype=float)
            slope, intercept = np.polyfit(x, y, 1)
            next_value = max(0.0, float(slope * len(monthly) + intercept))
            mean_value = float(y.mean())
            if mean_value > 0:
                mae = float(np.abs(y - (slope * x + intercept)).mean())
                error_pct = mae / mean_value * 100
            else:
                error_pct = 0.0
            next_month = (monthly["month_start"].max() + pd.offsets.MonthBegin(1)).strftime("%B %Y")
            c1, c2, c3 = st.columns(3)
            c1.metric(f"Baseline Forecast — {next_month}", money(next_value))
            c2.metric("Historical Monthly Average", money(mean_value))
            c3.metric("In-Sample Mean Absolute Error", f"{error_pct:.1f}% of average" if mean_value > 0 else "Not available")
            forecast_row = pd.DataFrame({"month_start": [monthly["month_start"].max() + pd.offsets.MonthBegin(1)], "amount": [next_value], "record_type": ["Forecast"]})
            history_plot = monthly.rename(columns={"month_start": "Month", "amount": "Amount"})[["Month", "Amount"]].copy()
            history_plot["Record Type"] = "Actual"
            forecast_plot = forecast_row.rename(columns={"month_start": "Month", "amount": "Amount", "record_type": "Record Type"})
            st.plotly_chart(px.line(pd.concat([history_plot, forecast_plot], ignore_index=True), x="Month", y="Amount", color="Record Type", markers=True, title="Actual Collection and Next-Month Baseline Forecast"), use_container_width=True)
            st.caption("This is a simple linear-trend baseline, not a guarantee. Forecast quality is limited when there are few months, seasonal changes, missing records, or one-off payments. The displayed error is measured on the same historical data used to fit the trend.")
        else:
            st.info(f"Only {len(monthly)} month(s) of data are available. At least 3 months are needed to show the baseline forecast; 12 or more months are recommended for more meaningful analysis.")
        st.subheader("Potential Outlier Detection")
        values = all_transactions["amount"].dropna()
        if len(values) >= 4:
            q1, q3 = values.quantile([0.25, 0.75])
            iqr = q3 - q1
            lower, upper = q1 - 1.5 * iqr, q3 + 1.5 * iqr
            outliers = all_transactions[(all_transactions["amount"] < lower) | (all_transactions["amount"] > upper)].copy()
            st.caption(f"IQR rule bounds: {money(lower)} to {money(upper)}. Flagged records require human review; they are not necessarily errors.")
            if outliers.empty:
                st.success("No potential outliers were detected by the IQR rule.")
            else:
                st.dataframe(outliers[["entry_date", "payment_type", "amount"]], use_container_width=True, hide_index=True)
        else:
            st.info("At least 4 transaction records are needed for basic IQR outlier screening.")
        st.download_button("Download Monthly Forecast Source Data", monthly.to_csv(index=False).encode("utf-8-sig"), file_name="monthly_collection_history.csv", mime="text/csv")

# =========================================================
# DATA IMPORT & EXPORT
# =========================================================
elif page == "Data Import & Export":
    st.header("📁 Data Import & Export")
    st.write("Download existing records for analysis in Excel, Power BI, SQL tools, or Python. This page does not insert uploaded rows into the live accounts database.")
    start, end = month_dates(year, month)
    export_options = {
        "Collections": read_query("SELECT * FROM transactions WHERE entry_date BETWEEN ? AND ? ORDER BY entry_date", (start, end)),
        "Expenses": read_query("SELECT * FROM expenses WHERE entry_date BETWEEN ? AND ? ORDER BY entry_date", (start, end)),
        "Refunds": get_refund_data(year, month),
        "Refund Payments": read_query("SELECT * FROM refund_payments WHERE entry_date BETWEEN ? AND ? ORDER BY entry_date", (start, end)),
    }
    dataset_name = st.selectbox("Choose Dataset", list(export_options.keys()))
    export_df = export_options[dataset_name]
    st.write(f"Records available: {len(export_df):,}")
    st.dataframe(export_df, use_container_width=True, hide_index=True)
    st.download_button("Download Selected Dataset as CSV", export_df.to_csv(index=False).encode("utf-8-sig"), file_name=f"{dataset_name.lower().replace(' ', '_')}_{year}_{month:02d}.csv", mime="text/csv")

# =========================================================
# DAILY PDF REPORT
# =========================================================
elif page == "Daily PDF Report":
    st.header("📄 Daily Cash & Online Report")
    report_date = st.date_input("Select Report Date", value=today, key="report_date")
    day_df = read_query("SELECT * FROM transactions WHERE entry_date=? ORDER BY id", (report_date.isoformat(),))
    start, end = report_date.isoformat(), report_date.isoformat()
    day_expenses = read_query("SELECT * FROM expenses WHERE entry_date=? ORDER BY id", (start,))
    day_refunds = read_query("SELECT * FROM refund_payments WHERE entry_date=? ORDER BY id", (start,))
    day_cash = day_df[day_df["payment_type"] == "Cash"] if not day_df.empty else day_df
    day_online = day_df[day_df["payment_type"] == "Online"] if not day_df.empty else day_df
    cash_in = float(day_cash["amount"].sum()) if not day_cash.empty else 0.0
    online_in = float(day_online["amount"].sum()) if not day_online.empty else 0.0
    cash_exp = float(day_expenses.loc[day_expenses.payment_type == "Cash", "amount"].sum()) if not day_expenses.empty else 0.0
    online_exp = float(day_expenses.loc[day_expenses.payment_type == "Online", "amount"].sum()) if not day_expenses.empty else 0.0
    cash_ref = float(day_refunds.loc[day_refunds.payment_type == "Cash", "amount"].sum()) if not day_refunds.empty else 0.0
    online_ref = float(day_refunds.loc[day_refunds.payment_type == "Online", "amount"].sum()) if not day_refunds.empty else 0.0

    c1, c2, c3 = st.columns(3)
    c1.metric("Cash Collection", money(cash_in))
    c2.metric("Online Collection", money(online_in))
    c3.metric("Total Collection", money(cash_in + online_in))
    c1, c2 = st.columns(2)
    c1.metric("Cash Expenses + Refunds", money(cash_exp + cash_ref))
    c2.metric("Online Expenses + Refunds", money(online_exp + online_ref))

    if not day_df.empty:
        st.subheader("Collection Entries")
        st.dataframe(day_df[["payer", "payment_type", "payment_mode", "amount"]], use_container_width=True, hide_index=True)
        st.subheader("Cash Note & Coin Details")
        note_rows = []
        for label, column, value in DENOMINATIONS:
            quantity = int(day_cash[column].fillna(0).sum()) if not day_cash.empty else 0
            note_rows.append({"Denomination": label, "Quantity": quantity, "Total Value (₹)": quantity * value})
        st.dataframe(pd.DataFrame(note_rows), use_container_width=True, hide_index=True)
    if not day_expenses.empty:
        st.subheader("Expense / Payment Entries")
        st.dataframe(day_expenses, use_container_width=True, hide_index=True)
    if not day_refunds.empty:
        st.subheader("Refunds Paid")
        st.dataframe(day_refunds, use_container_width=True, hide_index=True)

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 17)
    pdf.cell(0, 12, "FIVE POINT EDUCATION CENTRE", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 10, "DAILY CASH & ONLINE REPORT", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=10)
    pdf.cell(0, 8, f"Date: {report_date.strftime('%d-%m-%Y')}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)
    pdf.cell(0, 8, f"Cash Collection: INR {cash_in:,.2f}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 8, f"Online Collection: INR {online_in:,.2f}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 8, f"Cash Expenses + Refunds: INR {cash_exp + cash_ref:,.2f}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 8, f"Online Expenses + Refunds: INR {online_exp + online_ref:,.2f}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 8, f"Total Collection: INR {cash_in + online_in:,.2f}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 8, "COLLECTION TRANSACTIONS", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=9)
    if day_df.empty:
        pdf.cell(0, 7, "No collection entries.", new_x="LMARGIN", new_y="NEXT")
    else:
        for _, row in day_df.iterrows():
            name = str(row["payer"] or "Not specified").encode("latin-1", errors="replace").decode("latin-1")
            pdf.multi_cell(0, 7, f'{name[:50]} | {row["payment_type"]} | INR {float(row["amount"]):,.2f}')
    pdf.ln(3)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 8, "EXPENSE / PAYMENT TRANSACTIONS", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=9)
    if day_expenses.empty:
        pdf.cell(0, 7, "No expense entries.", new_x="LMARGIN", new_y="NEXT")
    else:
        for _, row in day_expenses.iterrows():
            name = str(row["payee"]).encode("latin-1", errors="replace").decode("latin-1")
            pdf.multi_cell(0, 7, f'{name[:45]} | {row["category"]} | {row["payment_type"]} | INR {float(row["amount"]):,.2f}')
    pdf_bytes = bytes(pdf.output())
    st.download_button("📥 Download Daily PDF Report", data=pdf_bytes,
                       file_name=f"FivePoint_Daily_Report_{report_date.isoformat()}.pdf",
                       mime="application/pdf", use_container_width=True)
    st.success("PDF ready! Download it and print a hard copy.")

st.divider()
st.caption("Five Point Education Centre | Collection & Accounts Management System")
