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

    # --- SECTION 1: ANGEL INVESTORS ---
    Story.append(Paragraph("Angel Investors & Networks (£2,000 - £10,000 Tickets)", h1))
    
    Story.append(Paragraph("1. SFC Capital", h2))
    Story.append(Paragraph("<b>Address:</b> Citibase Holborn, Fox Court, 14 Grays Inn Rd, London, WC1X 8HN<br/>"
                           "<b>Phone:</b> 0333 335 5834<br/>"
                           "<b>Email:</b> info@sfccapital.com<br/>"
                           "<b>People to contact:</b> Stephen Page, Joseph Zipfel, Ed Stevenson", p_style))
    Story.append(Spacer(1, 10))

    Story.append(Paragraph("2. Angel Investment Network", h2))
    Story.append(Paragraph("<b>Address:</b> Suite 16, Parsons Green House, 27-31 Parsons Green Lane, London, SW6 4HH<br/>"
                           "<b>Phone:</b> (Contact via platform/email)<br/>"
                           "<b>Email:</b> info@angelinvestmentnetwork.co.uk<br/>"
                           "<b>People to contact:</b> Mike Lebus, James Badgett, Sam Louis", p_style))
    Story.append(Spacer(1, 10))
    
    Story.append(Paragraph("3. Odin (Angel Syndicate Platform)", h2))
    Story.append(Paragraph("<b>Address:</b> London (Remote/Platform first)<br/>"
                           "<b>Phone:</b> (Contact via platform)<br/>"
                           "<b>Email:</b> hello@joinodin.com<br/>"
                           "<b>People to contact:</b> Mary Lin, Patrick Ryan", p_style))
    Story.append(Spacer(1, 10))
    
    Story.append(Paragraph("4. Envestors", h2))
    Story.append(Paragraph("<b>Address:</b> 3rd Floor, 60 Cannon Street, London, EC4N 6NP<br/>"
                           "<b>Phone:</b> +44 (0) 20 7240 0202<br/>"
                           "<b>Email:</b> info@envestors.co.uk<br/>"
                           "<b>People to contact:</b> Oliver Woolley, Scott Haughton", p_style))
    Story.append(Spacer(1, 10))

    Story.append(Paragraph("5. Newable Ventures / Angel Academe", h2))
    Story.append(Paragraph("<b>Address:</b> 140 Aldersgate St, Barbican, London EC1A 4HY<br/>"
                           "<b>Phone:</b> +44 (0) 20 7253 2222<br/>"
                           "<b>Email:</b> ventures@newable.co.uk<br/>"
                           "<b>People to contact:</b> Simon Hopkins, Sarah Turner", p_style))
    Story.append(Spacer(1, 20))

    # --- SECTION 2: INSTITUTIONAL VCS ---
    Story.append(Paragraph("Institutional VCs & Accelerators (£1 Million+ Tickets)", h1))
    
    Story.append(Paragraph("1. Seedcamp", h2))
    Story.append(Paragraph("<b>Address:</b> 16 Great Queen Street, London, WC2B 5AH<br/>"
                           "<b>Phone:</b> +44 20 3936 2828<br/>"
                           "<b>Email:</b> info@seedcamp.com<br/>"
                           "<b>People to contact:</b> Reshma Sohoni, Carlos Eduardo Espinal, Tom Wilson", p_style))
    Story.append(Spacer(1, 10))

    Story.append(Paragraph("2. Founders Factory", h2))
    Story.append(Paragraph("<b>Address:</b> Level 7, Arundel Street Building, 180 Strand, London, WC2R 3DA<br/>"
                           "<b>Phone:</b> (Contact via platform/email)<br/>"
                           "<b>Email:</b> hello@foundersfactory.com<br/>"
                           "<b>People to contact:</b> Brent Hoberman, Henry Lane Fox, Louis Warner", p_style))
    Story.append(Spacer(1, 10))
    
    Story.append(Paragraph("3. LocalGlobe", h2))
    Story.append(Paragraph("<b>Address:</b> Phoenix Court, 2 Brill Place, London NW1 1DX<br/>"
                           "<b>Phone:</b> (Contact via email or intro)<br/>"
                           "<b>Email:</b> info@localglobe.vc<br/>"
                           "<b>People to contact:</b> Robin Klein, Saul Klein, Suzanne Ashkoo", p_style))
    Story.append(Spacer(1, 10))

    Story.append(Paragraph("4. Balderton Capital", h2))
    Story.append(Paragraph("<b>Address:</b> 28B King John Court, London EC2A 3EZ<br/>"
                           "<b>Phone:</b> +44 20 7016 6800<br/>"
                           "<b>Email:</b> Contact via founders portal on website<br/>"
                           "<b>People to contact:</b> Suranga Chandratillake, Bernard Liautaud", p_style))
    Story.append(Spacer(1, 10))

    Story.append(Paragraph("5. Octopus Ventures", h2))
    Story.append(Paragraph("<b>Address:</b> 33 Holborn, London EC1N 2HT<br/>"
                           "<b>Phone:</b> +44 800 316 2295<br/>"
                           "<b>Email:</b> hello@octopusventures.com<br/>"
                           "<b>People to contact:</b> Alliott Cole, Zihao Xu", p_style))
    Story.append(Spacer(1, 20))

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
