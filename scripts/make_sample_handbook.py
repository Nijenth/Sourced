"""Generate the sample HR handbook PDF used by the demo.

Brightleaf Studio is a fictional company. Every policy, figure and contact in
this handbook is invented for demonstration purposes.

Run:  python scripts/make_sample_handbook.py
"""
from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

OUT = Path(__file__).resolve().parent.parent / "data" / "sample" / "Brightleaf_Studio_Employee_Handbook.pdf"

GREEN = colors.HexColor("#1F5C46")
INK = colors.HexColor("#1C1C1C")
MUTED = colors.HexColor("#6B6B6B")

styles = getSampleStyleSheet()
H1 = ParagraphStyle("H1", parent=styles["Heading1"], fontName="Helvetica-Bold", fontSize=20, leading=24, textColor=GREEN, spaceAfter=10)
H2 = ParagraphStyle("H2", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=13, leading=17, textColor=INK, spaceBefore=10, spaceAfter=4)
BODY = ParagraphStyle("Body", parent=styles["BodyText"], fontName="Helvetica", fontSize=10.5, leading=15, textColor=INK, spaceAfter=7)
BULLET = ParagraphStyle("Bullet", parent=BODY, leftIndent=14, bulletIndent=4, spaceAfter=3)
COVER_T = ParagraphStyle("CoverT", parent=H1, fontSize=30, leading=36, alignment=TA_CENTER)
COVER_S = ParagraphStyle("CoverS", parent=BODY, fontSize=13, leading=18, alignment=TA_CENTER, textColor=MUTED)


def p(text: str):
    return Paragraph(text, BODY)


def bullets(items: list[str]):
    return [Paragraph(item, BULLET, bulletText="•") for item in items]


def table(rows: list[list[str]], widths: list[float]):
    data = [[Paragraph(c, BODY) for c in r] for r in rows]
    t = Table(data, colWidths=[w * mm for w in widths], hAlign="LEFT")
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8F0EC")),
                ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor("#C9D6CF")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    return t


def footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(MUTED)
    canvas.drawString(20 * mm, 12 * mm, "Brightleaf Studio is a fictional company. Sample document for demo purposes only.")
    canvas.drawRightString(190 * mm, 12 * mm, f"Page {doc.page}")
    canvas.restoreState()


def build() -> Path:
    s: list = []

    # Page 1: cover
    s += [Spacer(1, 70 * mm), Paragraph("Brightleaf Studio", COVER_T), Spacer(1, 6 * mm),
          Paragraph("Employee Handbook", COVER_S), Paragraph("Version 3.2 · Effective 1 January 2026", COVER_S),
          Spacer(1, 40 * mm),
          Paragraph("Owned by the People team · people@brightleaf.example", COVER_S), PageBreak()]

    # Page 2: welcome
    s += [Paragraph("Welcome to Brightleaf", H1),
          p("Brightleaf Studio is a 64-person design and software agency with a studio in Austin, Texas and team members "
            "working remotely across the United States. We build websites, mobile apps and brand systems for clients in "
            "education, healthcare and retail."),
          p("This handbook explains how we work together: your time off, pay and benefits, expenses, equipment, and what "
            "to do when something goes wrong. It applies to all full-time and part-time employees. Contractors are covered "
            "by their own agreements and only the Code of Conduct section applies to them."),
          p("If this handbook and your employment contract disagree, your contract wins. The People team reviews the "
            "handbook every January and announces changes in the #announcements channel at least 30 days before they "
            "take effect."),
          Paragraph("How to use this handbook", H2),
          *bullets(["Each section starts with the rule, then the details and exceptions.",
                    "Questions about a policy go to your manager first, then the People team.",
                    "The current version always lives on the intranet under People › Handbook."]),
          PageBreak()]

    # Page 3: contents
    s += [Paragraph("Contents", H1),
          table([["Section", "Page"],
                 ["1. Employment basics", "4"], ["2. Working hours and remote work", "5"], ["3. Annual leave and holidays", "6"],
                 ["4. Sick leave", "7"], ["5. Parental and family leave", "8"], ["6. Pay and benefits", "9"],
                 ["7. Expenses and travel", "10"], ["8. Learning and development", "11"], ["9. Equipment and IT security", "12"],
                 ["10. Performance and growth", "13"], ["11. Code of conduct and reporting concerns", "14"],
                 ["12. Leaving Brightleaf", "15"], ["13. Who to contact", "16"]], [120, 25]),
          PageBreak()]

    # Page 4: employment basics
    s += [Paragraph("1. Employment basics", H1),
          Paragraph("Employment types", H2),
          *bullets(["<b>Full-time:</b> 40 hours a week, eligible for every benefit in this handbook.",
                    "<b>Part-time:</b> 20 to 32 hours a week. Leave and the learning budget are pro-rated to your hours.",
                    "<b>Contractors:</b> engaged through a statement of work, not covered by benefits."]),
          Paragraph("Probation", H2),
          p("Every new employee has a probation period of 90 days (three months). Your manager meets you at 30, 60 and 90 "
            "days to review how things are going. Probation can be extended once, by up to 60 days, if both of you agree "
            "more time is needed. You can take annual leave during probation, but remote work from outside the Austin area "
            "starts only after probation ends."),
          Paragraph("Notice periods", H2),
          table([["Situation", "Notice required (either side)"],
                 ["During probation", "1 week"],
                 ["After probation", "4 weeks"],
                 ["Lead, Principal and Director roles", "8 weeks"]], [80, 80]),
          PageBreak()]

    # Page 5: hours and remote
    s += [Paragraph("2. Working hours and remote work", H1),
          Paragraph("Core hours", H2),
          p("Core collaboration hours are 10:00 to 16:00 in your local time zone, Monday to Friday. Outside core hours you can "
            "arrange your day as you like, as long as your team knows when you are available. Meetings should not be booked "
            "before 9:00 or after 17:00 in the attendee's time zone."),
          Paragraph("Hybrid schedule", H2),
          p("Employees who live within 50 miles of the Austin studio work hybrid: in the studio on Tuesdays and Thursdays and "
            "wherever they prefer the rest of the week. Fully remote work is available after probation with your manager's "
            "approval."),
          Paragraph("Working from another country", H2),
          p("You may work from outside the United States for up to 30 days per calendar year. Tell the People team at least "
            "two weeks before you travel so they can check tax and immigration rules. Some client projects forbid access to "
            "their systems from abroad; your project lead will tell you if that applies."),
          Paragraph("Home office support", H2),
          *bullets(["A one-time home office stipend of $500 for a desk, chair or monitor.",
                    "$40 a month toward your internet bill, paid with your salary."]),
          PageBreak()]

    # Page 6: annual leave
    s += [Paragraph("3. Annual leave and holidays", H1),
          Paragraph("Annual leave", H2),
          p("Full-time employees get 24 days of paid annual leave each calendar year. Leave accrues at 2 days per month, and "
            "part-time employees receive a pro-rated amount based on their contracted hours. New starters accrue from their "
            "first day."),
          p("Request leave in the HR portal. For absences longer than three working days, submit your request at least two "
            "weeks in advance. Your manager responds within three working days."),
          Paragraph("Carrying leave over", H2),
          p("You can carry up to 5 unused days into the next year. Carried-over days must be used by 31 March or they expire. "
            "Leave cannot be exchanged for pay, except when you leave Brightleaf."),
          Paragraph("Company holidays", H2),
          p("The studio closes for 10 company holidays: New Year's Day, Martin Luther King Jr. Day, Memorial Day, Juneteenth, "
            "Independence Day, Labor Day, Thanksgiving Day, the day after Thanksgiving, Christmas Eve and Christmas Day. "
            "The studio also closes between 26 and 31 December; these days do not come out of your annual leave."),
          PageBreak()]

    # Page 7: sick leave
    s += [Paragraph("4. Sick leave", H1),
          p("Every employee gets 10 days of paid sick leave per calendar year. Sick leave does not carry over and is not paid "
            "out when you leave."),
          Paragraph("Telling us you are sick", H2),
          p("Message your manager before 10:00 on the first day you are off, then log the absence in the HR portal when you "
            "are back. If you are off for more than three consecutive working days, please send a doctor's note to the People "
            "team."),
          Paragraph("Mental health days", H2),
          p("Mental health is health. You can use sick days for your mental health without giving a reason, and you do not "
            "need a doctor's note for these days unless the absence goes beyond three consecutive working days."),
          Paragraph("Longer illness", H2),
          p("If you need more than 10 days in a year, talk to the People team. We offer short-term disability cover that pays "
            "60% of your salary for up to 12 weeks after the first 10 days of absence."),
          PageBreak()]

    # Page 8: parental and family
    s += [Paragraph("5. Parental and family leave", H1),
          Paragraph("Parental leave", H2),
          p("Employees with at least six months of service are eligible for paid parental leave. The birthing parent receives "
            "16 weeks at full pay. Non-birthing parents, including adoptive and foster parents, receive 8 weeks at full pay. "
            "Parental leave must be taken within 12 months of the birth or placement and can be split into up to two blocks."),
          p("Please tell your manager and the People team at least 8 weeks before your planned leave starts. When you return, "
            "you can work a reduced schedule of four days a week at full pay for your first four weeks back."),
          Paragraph("Bereavement leave", H2),
          *bullets(["5 paid days for the death of an immediate family member (partner, child, parent, sibling).",
                    "2 paid days for extended family members and close friends."]),
          Paragraph("Jury duty and caregiving", H2),
          p("Jury duty is paid at full salary for up to 10 working days. Employees may also take up to 5 days of paid "
            "caregiver leave per year to look after a sick family member."),
          PageBreak()]

    # Page 9: pay and benefits
    s += [Paragraph("6. Pay and benefits", H1),
          Paragraph("Payday", H2),
          p("Salaries are paid monthly on the last working day of the month by direct deposit. Payslips are available in the "
            "HR portal two days before payday."),
          Paragraph("Salary reviews", H2),
          p("Salaries are reviewed once a year in April, using the December performance review and market data. Promotions "
            "made in December take effect on 1 January."),
          Paragraph("Health insurance", H2),
          p("Brightleaf pays 90% of the medical, dental and vision premium for employees and 70% for dependants. Cover starts "
            "on the first day of the month after your start date."),
          Paragraph("Retirement", H2),
          p("We match 401(k) contributions up to 4% of your salary. The match vests immediately."),
          Paragraph("Other benefits", H2),
          *bullets(["$50 a month wellness allowance for a gym, classes or apps.",
                    "Life insurance worth twice your annual salary, at no cost to you.",
                    "Brightleaf does not currently offer stock options or equity."]),
          PageBreak()]

    # Page 10: expenses
    s += [Paragraph("7. Expenses and travel", H1),
          p("Submit expenses in the Ledgerly app within 30 days of the purchase, with a photo of the receipt. Reimbursements "
            "are paid with the next salary run. Expenses submitted after 90 days will not be reimbursed."),
          Paragraph("Travel limits", H2),
          table([["Item", "Limit"],
                 ["Hotel", "$220 per night (up to $300 in New York or San Francisco)"],
                 ["Meals, domestic travel", "$60 per day"],
                 ["Meals, international travel", "$85 per day"],
                 ["Flights under 6 hours", "Economy"],
                 ["Flights of 6 hours or more", "Premium economy"],
                 ["Ground transport", "Public transit or standard rideshare"]], [60, 100]),
          Paragraph("Client entertainment", H2),
          p("Client meals and events over $150 need your manager's approval before you book. Alcohol is reimbursed only at "
            "client events. Gifts to clients are limited to $75 per person per year."),
          Paragraph("What we do not reimburse", H2),
          *bullets(["Traffic or parking fines.", "Upgrades to flights or hotels beyond the limits above.",
                    "Personal travel added to a business trip."]),
          PageBreak()]

    # Page 11: learning
    s += [Paragraph("8. Learning and development", H1),
          p("Every full-time employee has a learning budget of $1,500 per calendar year, plus 3 paid learning days to spend on "
            "courses, conferences or study. Part-time employees receive a pro-rated budget."),
          Paragraph("What the budget covers", H2),
          *bullets(["Online and in-person courses related to your role or your next role.",
                    "Books, e-learning subscriptions and certification exam fees.",
                    "Conference tickets. Conference travel comes from the team travel budget, not your learning budget."]),
          Paragraph("Approvals", H2),
          p("Anything over $300 needs your manager's approval before you buy. Unused budget does not carry over to the next "
            "year."),
          Paragraph("Repayment", H2),
          p("If you leave Brightleaf within 12 months of completing a single course that cost more than $1,000, you will be "
            "asked to repay 50% of its cost. This does not apply if Brightleaf ends your employment."),
          PageBreak()]

    # Page 12: equipment & security
    s += [Paragraph("9. Equipment and IT security", H1),
          Paragraph("Your equipment", H2),
          p("You will receive a MacBook Pro or ThinkPad laptop on your first day, and a replacement every three years. You can "
            "also order one external monitor, a keyboard and a mouse through the IT portal."),
          Paragraph("Security rules", H2),
          *bullets(["Two-factor authentication is mandatory on every work account.",
                    "Store all passwords in the company password manager.",
                    "Never keep client data on personal devices or personal cloud storage.",
                    "Lock your screen whenever you leave your laptop."]),
          Paragraph("Lost or stolen devices", H2),
          p("Report a lost or stolen laptop or phone to it@brightleaf.example within 24 hours, even outside working hours. IT "
            "will wipe the device remotely. You will not be charged for a lost device unless it was lost through repeated "
            "negligence."),
          Paragraph("Personal use", H2),
          p("Reasonable personal use of your laptop is fine, such as personal email or browsing. Do not install pirated "
            "software, games with anti-cheat drivers, or cryptocurrency mining tools."),
          PageBreak()]

    # Page 13: performance
    s += [Paragraph("10. Performance and growth", H1),
          Paragraph("Reviews", H2),
          p("Performance reviews take place twice a year, in June and December. Each review includes a self-assessment, "
            "feedback from two peers you choose, and a conversation with your manager. The December review informs promotions "
            "and the April salary review."),
          Paragraph("One-to-ones", H2),
          p("You meet your manager one-to-one every two weeks. These meetings are yours: bring blockers, ideas and career "
            "questions."),
          Paragraph("Promotions", H2),
          p("Promotions are decided once a year in December by a panel of studio leads, based on our published career "
            "framework. You can ask your manager to put you forward."),
          Paragraph("Performance improvement plans", H2),
          p("If performance falls below expectations, your manager will first raise it in your one-to-ones. If things do not "
            "improve, we use a written performance improvement plan lasting 60 days, with clear goals and weekly check-ins."),
          PageBreak()]

    # Page 14: conduct
    s += [Paragraph("11. Code of conduct and reporting concerns", H1),
          p("We expect everyone to treat colleagues, clients and partners with respect. Harassment, discrimination and "
            "bullying are not tolerated, whether in the studio, online, at client sites or at company events."),
          Paragraph("How to report a concern", H2),
          *bullets(["Talk to your manager or any studio lead.",
                    "Email the People team at people@brightleaf.example.",
                    "Use the anonymous SpeakUp form on the intranet. Reports go to an external provider, not to Brightleaf staff."]),
          p("The People team starts an investigation within 5 working days of a report and keeps the person who raised it "
            "informed. Retaliation against anyone who raises a concern in good faith is itself a serious breach of this code."),
          Paragraph("Gifts and conflicts of interest", H2),
          p("Declare any gift from a client or supplier worth more than $75 to the People team. Tell your manager about any "
            "side project or outside job that could compete with Brightleaf or its clients."),
          PageBreak()]

    # Page 15: leaving
    s += [Paragraph("12. Leaving Brightleaf", H1),
          p("To resign, email your manager and the People team with your intended last day, giving the notice period that "
            "applies to you (see Employment basics)."),
          Paragraph("Before your last day", H2),
          *bullets(["Hand over your projects using the handover template on the intranet.",
                    "Return your laptop and other equipment within 5 working days of your last day. IT sends a prepaid box to remote employees.",
                    "Book an optional exit interview with the People team."]),
          Paragraph("Final pay", H2),
          p("Your final salary includes payment for any unused annual leave, including carried-over days. Unused sick leave "
            "and learning budget are not paid out."),
          Paragraph("References", H2),
          p("Brightleaf references confirm only your job title and dates of employment. Managers may not give personal "
            "references on company letterhead."),
          PageBreak()]

    # Page 16: contacts
    s += [Paragraph("13. Who to contact", H1),
          table([["Topic", "Contact"],
                 ["Leave, benefits, payroll, policies", "People team · people@brightleaf.example"],
                 ["Laptops, accounts, lost devices", "IT · it@brightleaf.example"],
                 ["Expenses and reimbursements", "Finance · finance@brightleaf.example"],
                 ["Anonymous concerns", "SpeakUp form on the intranet"],
                 ["Studio access and facilities", "Studio manager · studio@brightleaf.example"]], [70, 95]),
          Spacer(1, 6 * mm),
          p("The People team is available Monday to Friday, 9:00 to 17:00 Central Time, and aims to reply within one working day.")]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(OUT), pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=20 * mm,
                            bottomMargin=22 * mm, title="Brightleaf Studio Employee Handbook (sample)",
                            author="Brightleaf Studio (fictional)")
    doc.build(s, onFirstPage=footer, onLaterPages=footer)
    return OUT


if __name__ == "__main__":
    path = build()
    print(f"Wrote {path}")
