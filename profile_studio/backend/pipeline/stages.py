"""The 32 call stages, in order.

Stage n lines up with the audit report's top-level question with order n, so the
audit's status, reasoning and timestamps for that question belong to stage n.
"""

from __future__ import annotations

STAGES: list[dict] = [
    {"n": 1, "key": "dpa", "name": "DPA", "group": "Opening",
     "desc": "Data protection: advisor confirms name, date of birth, first line of address, postcode, email; call recording and privacy policy statements."},
    {"n": 2, "key": "intro", "name": "Intro", "group": "Opening",
     "desc": "Advisor introduces themselves and the company, the regulator (FCA), free debt advice options, confidentiality, right to cancel."},
    {"n": 3, "key": "fact_find", "name": "Fact Find", "group": "Fact Find",
     "desc": "Biggest concern, what is happening with the debts, how long, trigger point, what they have tried, previous solutions, impact on daily life, family/friends help."},
    {"n": 4, "key": "vulnerability", "name": "Vulnerability Check", "group": "Fact Find",
     "desc": "TEXAS / vulnerability: physical or mental health, disability, addiction, bereavement, communication needs, medical support, third-party help, consent to note vulnerability."},
    {"n": 5, "key": "affordability", "name": "Affordability", "group": "Fact Find",
     "desc": "Before any solution, what monthly amount the customer feels they can afford."},
    {"n": 6, "key": "loan_model", "name": "Loan Model", "group": "Fact Find",
     "desc": "Further lending / consolidation loan: is borrowing realistic, recent applications, loan amount, term, interest, monthly payment, why declined."},
    {"n": 7, "key": "aryza_debt_check", "name": "Aryza Debt Check", "group": "Debts",
     "desc": "Advisor goes through each debt on the system (Aryza): creditor, balance, contractual monthly payment, arrears."},
    {"n": 8, "key": "ccj_guarantor_joint_priority", "name": "CCJ Guarantor Joint Debt Priority Bills", "group": "Debts",
     "desc": "Any CCJs, guarantor debts, joint debts, and whether priority bills (rent/mortgage, council tax, energy) are up to date."},
    {"n": 9, "key": "additional_debts", "name": "Additional Debt List", "group": "Debts",
     "desc": "Scripted additional-debt checklist: overdrafts, HMRC, council tax, DWP, court fines, parking fines, rent arrears, student loans, HP/car arrears, recent spending, preferential payments."},
    {"n": 10, "key": "total_debt", "name": "Total Debt and Monthly Payment", "group": "Debts",
     "desc": "Confirms the total debt level and total contractual monthly payments."},
    {"n": 11, "key": "personal_details", "name": "Personal Details", "group": "Personal",
     "desc": "Partner/marital status, dependants, household, housing, employment, self-employment, directorships, benefits."},
    {"n": 12, "key": "change_in_circumstance", "name": "Change in Circumstance", "group": "Personal",
     "desc": "Scripted future-changes question: any expected change in income, household, job, health in the future."},
    {"n": 13, "key": "assets", "name": "Assets", "group": "Personal",
     "desc": "Property, vehicles, finance agreements, savings, investments, crypto, pensions, lump sums, sold assets, claims."},
    {"n": 14, "key": "client_summary", "name": "Client Summary", "group": "Personal",
     "desc": "Advisor delivers a single summary of the client's situation before any solution and checks nothing is missed."},
    {"n": 15, "key": "income", "name": "Income", "group": "Income & Expenditure",
     "desc": "All incomes: wages, deductions, benefits, Universal Credit, child benefit, maintenance, partner contributions."},
    {"n": 16, "key": "expenditure", "name": "Expenditure List", "group": "Income & Expenditure",
     "desc": "All expenditure items: rent/mortgage, utilities, council tax, water, TV licence, phone/internet, food, transport, childcare, pets, clothing, etc."},
    {"n": 17, "key": "expenditure_manipulation", "name": "Expenditure Manipulation", "group": "Income & Expenditure",
     "desc": "Whether the I&E was client-led or adjusted/steered by the advisor; mapping figures against the income and expenditure lists; customer pushback on changed figures."},
    {"n": 18, "key": "disposable_income", "name": "Disposable Income", "group": "Income & Expenditure",
     "desc": "Advisor confirms the final disposable income (income minus expenditure) before solutions."},
    {"n": 19, "key": "sol_do_nothing", "name": "Solution: Do Nothing & Self-Management", "group": "Solutions",
     "desc": "Do nothing / self-management / dealing directly with creditors explained."},
    {"n": 20, "key": "sol_breathing_space", "name": "Solution: Breathing Space", "group": "Solutions",
     "desc": "Debt Respite Scheme (Breathing Space) explained."},
    {"n": 21, "key": "sol_dmp", "name": "Solution: Debt Management Plan (DMP)", "group": "Solutions",
     "desc": "Debt Management Plan explained."},
    {"n": 22, "key": "sol_admin_order", "name": "Solution: Administration Order", "group": "Solutions",
     "desc": "Administration Order explained where the client qualifies."},
    {"n": 23, "key": "sol_dro", "name": "Solution: Debt Relief Order (DRO)", "group": "Solutions",
     "desc": "Debt Relief Order explained."},
    {"n": 24, "key": "sol_bankruptcy", "name": "Solution: Bankruptcy", "group": "Solutions",
     "desc": "Bankruptcy explained."},
    {"n": 25, "key": "sol_iva", "name": "Solution: IVA", "group": "Solutions",
     "desc": "Individual Voluntary Arrangement explained."},
    {"n": 26, "key": "sol_lending", "name": "Solution: Lending (Credit / Loan)", "group": "Solutions",
     "desc": "Lending / consolidation loan as a solution explained."},
    {"n": 27, "key": "sol_remortgage", "name": "Solution: Re-Mortgaging / Sale of Property", "group": "Solutions",
     "desc": "Re-mortgaging or sale of property explained (if the client owns property)."},
    {"n": 28, "key": "solution_summary", "name": "Solution Summary", "group": "Closing",
     "desc": "Client confirms which solution they have chosen and gives their own reasons for choosing/rejecting each."},
    {"n": 29, "key": "correct_solution", "name": "Correct Solution", "group": "Closing",
     "desc": "The final solution and whether it fits the client's circumstances."},
    {"n": 30, "key": "iva_info_warnings", "name": "IVA Info & Warnings", "group": "Closing",
     "desc": "If IVA recommended: all mandatory IVA information and warnings delivered (credit rating, term, fees, failure consequences)."},
    {"n": 31, "key": "documents_check", "name": "Documents Check", "group": "Closing",
     "desc": "Advisor requests supporting documents: ID, payslips, bank statements, benefit letters, creditor statements."},
    {"n": 32, "key": "consent_to_transfer", "name": "Consent to Transfer", "group": "Closing",
     "desc": "Customer made aware of / consents to transfer of the case (e.g. to the IVA provider / next team)."},
]

STAGE_BY_N = {s["n"]: s for s in STAGES}


def stage_list_text() -> str:
    return "\n".join(f'{s["n"]}. {s["name"]} - {s["desc"]}' for s in STAGES)
