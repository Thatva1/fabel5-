import os
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_JUSTIFY, TA_LEFT

def create_pdf(filename):
    doc = SimpleDocTemplate(filename, pagesize=A4,
                            rightMargin=40, leftMargin=40,
                            topMargin=40, bottomMargin=40)
    Story = []
    styles = getSampleStyleSheet()
    
    # Custom Styles
    styles.add(ParagraphStyle(name='CustomHeading1', parent=styles['Heading1'], spaceAfter=14))
    styles.add(ParagraphStyle(name='CustomHeading2', parent=styles['Heading2'], spaceAfter=10))
    styles.add(ParagraphStyle(name='CustomNormal', parent=styles['Normal'], spaceAfter=12, leading=14))
    styles.add(ParagraphStyle(name='BoldList', parent=styles['CustomNormal'], fontName='Helvetica-Bold'))

    h1 = styles['CustomHeading1']
    h2 = styles['CustomHeading2']
    p_style = styles['CustomNormal']

    # Every entry below was checked against the firm's OWN website on
    # 2 October 2026. Anything that could not be confirmed there — most phone
    # numbers, several e-mail addresses and some named people — was removed
    # rather than left in on trust. People move; re-check before sending.
    def firm(title, lines):
        Story.append(Paragraph(title, h2))
        Story.append(Paragraph("<br/>".join(lines), p_style))
        Story.append(Spacer(1, 10))

    Story.append(Paragraph(
        "<i>Checked against each firm's own website on 2 October 2026. Details that "
        "could not be confirmed there were removed. Most of these firms ask founders "
        "to apply through a form, not by e-mail or phone, so the route they state is "
        "listed first.</i>", p_style))
    Story.append(Spacer(1, 10))

    # --- SECTION 1: ANGEL INVESTORS ---
    Story.append(Paragraph("Angel Investors &amp; Networks (£2,000 - £10,000 Tickets)", h1))

    firm("1. SFC Capital", [
        "<b>What it is:</b> SEIS and EIS seed fund. States it invests £100k-£300k for 10%-20%.",
        "<b>How to apply:</b> register at portal.sfccapital.com/register/startup",
        "<b>Requirement stated:</b> the company must qualify for SEIS.",
        "<b>Address:</b> Fox Court, 14 Grays Inn Rd, London, WC1X 8HN",
        "<b>Email:</b> info@sfccapital.com",
    ])
    firm("2. Angel Investment Network", [
        "<b>What it is:</b> an online platform where founders post a pitch to registered angels. It is not itself an investor.",
        "<b>How to apply:</b> create a pitch with the online form at angelinvestmentnetwork.co.uk",
        "<b>Address / phone / email:</b> none shown on its website.",
    ])
    firm("3. Odin", [
        "<b>What it is:</b> a platform for running SPVs and angel syndicates. It is not itself an investor; it is a tool for collecting money from angels you have already found.",
        "<b>Founders:</b> Patrick Ryan, Mary Lin",
        "<b>Email:</b> hello@joinodin.com",
        "<b>Website:</b> joinodin.com",
    ])
    firm("4. Envestors", [
        "<b>What it is:</b> investment network and platform.",
        "<b>How to apply:</b> envestors.envestry.com/raising",
        "<b>People (from its website):</b> Oliver Woolley, Co-founder &amp; Executive Chair; Scott Haughton, Co-Founder &amp; Chief Operating Officer",
        "<b>Address shown on its website:</b> Envestors Limited, c/o Ballards LLP, Oakmoore Court, Kingswood Road, Hampton Lovett, Droitwich Spa, WR9 0QH",
    ])
    firm("5. Angel Academe", [
        "<b>What it is:</b> an EIS fund and angel network that backs female founders.",
        "<b>How to apply:</b> angelacademe.com/founders",
        "<b>Note:</b> a separate organisation from Newable. The two were listed together in the earlier version of this plan by mistake.",
    ])
    firm("6. Newable", [
        "<b>Address:</b> 140 Aldersgate Street, London EC1A 4HY",
        "<b>Phone:</b> 020 7260 3100",
        "<b>Note:</b> its website now describes buying established regulated businesses. No angel-network application route is shown there, so confirm it still funds start-ups before approaching.",
    ])
    Story.append(Spacer(1, 10))

    # --- SECTION 2: INSTITUTIONAL VCS ---
    Story.append(Paragraph("Institutional VCs &amp; Accelerators (£1 Million+ Tickets)", h1))

    firm("1. Seedcamp", [
        "<b>How to apply:</b> the \"Pitch Us\" page at seedcamp.com",
        "<b>Address:</b> 12 Little Portland Street, London W1W 8BJ",
        "<b>People (from its website):</b> Reshma Sohoni, Co-Founder &amp; Managing Partner; Carlos Eduardo Espinal, Managing Partner; Tom Wilson, Partner",
    ])
    firm("2. Founders Factory", [
        "<b>Address:</b> 180 Strand, 2 Arundel Street, London WC2R 3DA",
        "<b>People (from the Founders Forum Group website):</b> Brent Hoberman, Co-Founder &amp; Executive Chair; Henry Lane Fox, Co-Founder and CEO of Founders Factory",
        "<b>Website:</b> foundersfactory.com",
    ])
    firm("3. LocalGlobe (Phoenix Court)", [
        "<b>Address:</b> Phoenix Court, 2 Brill Place, London NW1 1DX",
        "<b>People (from its website):</b> Robin Klein, Saul Klein",
        "<b>Website:</b> phoenixcourt.vc (localglobe.vc redirects there). Contact page: phoenixcourt.vc/contact",
    ])
    firm("4. Balderton Capital", [
        "<b>How to approach:</b> its website asks founders to contact a member of the investment team directly, through balderton.com/team",
        "<b>Address:</b> The Stables, 28 Britannia Street, London WC1X 9JF",
        "<b>Phone:</b> +44 (0) 20 7016 6800",
        "<b>People (from its website):</b> Bernard Liautaud, Managing Partner; Suranga Chandratillake, Partner",
    ])
    firm("5. Octopus Ventures", [
        "<b>How to apply:</b> the pitch forms at octopusventures.com/contact (one for pre-seed, one for seed and later)",
        "<b>Address:</b> 33 Holborn, London EC1N 2HT",
        "<b>People (from its website):</b> Erin Platts, CEO",
    ])
    Story.append(Spacer(1, 10))

    # --- SECTION 3: SOCIAL MEDIA TEMPLATES ---
    Story.append(Paragraph("Social Media Connection Notes", h1))
    
    Story.append(Paragraph("X (Twitter) DM", h2))
    Story.append(Paragraph("Hey [Name], I'm building a trading research platform based in London. Most software hides its flaws; ours is built to flag stale data and overfitting. We just proved that 12 famous strategies lost to the index. Raising a seed round right now—would love to send you the deck if you're open to it!", p_style))
    
    Story.append(Paragraph("LinkedIn Connection Note (Under 300 characters)", h2))
    Story.append(Paragraph("Hi [Name], I'm building a systematic trading platform. In our 9-year backtest of 12 strategies, buy-and-hold actually beat all of them. We built this to flag bad data, not hide it. I’m currently raising a seed round and would love to connect and share our findings.", p_style))
    
    Story.append(Paragraph("Instagram Direct Message", h2))
    Story.append(Paragraph("Hey [Name]! I’ve been following your work. I’m currently raising a seed round for my new venture—a systematic trading research platform built on radical transparency (it caught 8 material bugs in standard trading models). Opening up early angel tickets to my network. Would love to send you our investor deck!", p_style))
    Story.append(Spacer(1, 20))
    
    # --- SECTION 4: EMAIL TEMPLATES ---
    Story.append(Paragraph("Email Templates", h1))

    Story.append(Paragraph("1. For Friends & Family (£2,000 - £10,000 Ask)", h2))
    email_ff = ("<b>Subject:</b> Seed funding round: Systematic trading research platform<br/><br/>"
                "Dear [Name],<br/><br/>"
                "I am reaching out to share an early-stage business venture I am building and to offer an investment opportunity. While we know each other personally, I want to approach this strictly as a formal business proposal.<br/><br/>"
                "I am currently raising a seed round for [Company Name], a systematic trading research platform. The platform is designed to rigorously test published trading strategies with radical transparency. Instead of hiding flawed results—which is common in this space—the software argues with its own conclusions by flagging stale data, mixed data, and overfitted numbers.<br/><br/>"
                "To give you an idea of our approach: in our recent 9-year backtest of 12 published strategies, our system proved that simply holding the index actually outperformed the strategies on absolute return. However, our system achieved roughly 60% of the maximum drawdown (meaning a significantly lower peak-to-trough fall). It is a decision-support tool built to be audited, not blindly trusted.<br/><br/>"
                "I am opening early angel/seed allocations with ticket sizes between £2,000 and £10,000 to my immediate network before pitching to wider institutional investors. The funding will be used to run proper walk-forward tests across different market regimes.<br/><br/>"
                "If this is something you might be interested in exploring, please let me know. I would be happy to send over the investor deck and the full backtest results, or schedule a brief 20-minute call to walk you through the business case.<br/><br/>"
                "Best regards,<br/>"
                "[Your Name]<br/>"
                "<i>Disclaimer: Research and decision-support software. Not financial advice, and not an offer or solicitation to buy or sell any security.</i>")
    Story.append(Paragraph(email_ff, p_style))
    Story.append(Spacer(1, 10))

    Story.append(Paragraph("2. For Professional Investors (£1M+ Seed Ask)", h2))
    email_pi = ("<b>Subject:</b> Systematic trading research — our backtest lost to the index, and we can prove why<br/><br/>"
                "Hi [Name],<br/><br/>"
                "I'm building [Company Name], a systematic trading research platform. I'm raising a £1M seed round and would value 20 minutes of your time.<br/><br/>"
                "The short version of where we are: we tested 12 published strategies across 528 instruments over nearly nine years. <b>Buy-and-hold beat every one of them on absolute return.</b> Our best configuration returned about 3.5 percentage points a year less, with roughly 15 points less drawdown.<br/><br/>"
                "I'm leading with that because it's the first thing you'd find, and because the thing I'm actually building is the reason I know it. Most trading research can't tell you when its own data is stale, mixed, or overfitted. Ours reports all three, per number, and the limitations section of our study was written by us rather than found by a reviewer.<br/><br/>"
                "If a research tool that argues with its own conclusions is interesting to you, I'd like to show you what it found — including the parts that didn't work.<br/><br/>"
                "Would [Day] or [Day] suit for a short call?<br/><br/>"
                "Best,<br/>"
                "[Your Name]<br/>"
                "[Phone] · [Link]<br/>"
                "<i>Research and decision-support software. Not financial advice, and not an offer or solicitation to buy or sell any security.</i>")
    Story.append(Paragraph(email_pi, p_style))

    doc.build(Story)

if __name__ == '__main__':
    create_pdf('Fundraising_Plan.pdf')
