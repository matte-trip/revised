import os
import feedparser
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timedelta
from dotenv import load_dotenv
from openai import OpenAI
from email.utils import formataddr

# Carica le variabili ambiente (.env in locale, su Railway userai env vars)
load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
EMAIL_TO = os.getenv("EMAIL_TO")

RSS_URL = "https://www.hdblog.it/feed/"  # puoi cambiare feed se preferisci
HOURS_BACK = 24  # finestra temporale

client = OpenAI(api_key=OPENAI_API_KEY)


def is_low_priority(title: str, description: str) -> bool:
    text = f"{title} {description}".lower()

    keywords = [
        # calcio / sport
        "calcio", "serie a", "champions", "europei", "mondiali",
        # moto
        " ducati", " yamaha", " kawasaki", "moto ", " motogp",
        # offerte / best buy
        "best buy", "offerta", "offerte", "sconto", "prezzo minimo",
        "minimo storico", "super prezzo", "super offerta", "promo ",
        # aspirapolvere / robot
        "aspirapolvere", "robot aspirapolvere", "roborock", "roomba",
        "dyson v", "folletto",
        # operatori telefonici
        " tim ", " vodafone", " iliad", " windtre", " ho. mobile", " very mobile",
        # assicurazioni auto
        "polizza auto", "assicurazione auto"
    ]

    return any(k in text for k in keywords)


def rewrite_title_and_summary(original_title: str, description: str) -> tuple[str, str]:
    """
    Chiama OpenAI per:
    - riscrivere il titolo in stile informativo
    - generare una mini-descrizione (max ~30 parole) solo se serve
    Restituisce (titolo_riscritto, descrizione_breve)
    """
    system_prompt = (
        "Sei un assistente che rielabora articoli tech per un digest email giornaliero.\n"
        "Per ogni articolo:\n"
        "- Riscrivi il titolo in modo chiaro, informativo e non commerciale.\n"
        "- Usa il formato 'Prodotto/Azienda – breve descrizione tecnica'.\n"
        "- Evita parole come 'offerta', 'super sconto', 'best buy', 'prezzo minimo'.\n"
        "- Poi, se necessario, aggiungi una mini-descrizione (massimo 30 parole) che chiarisca il contenuto.\n"
        "- Se il titolo originale è già chiaro, rendilo solo più ordinato e compatto.\n"
        "Rispondi nel seguente formato esatto:\n"
        "TITOLO: <titolo riscritto>\n"
        "DESCRIZIONE: <descrizione breve oppure '-' se non necessaria>\n"
    )

    user_prompt = (
        f"Titolo originale: {original_title}\n"
        f"Descrizione/estratto: {description}\n"
    )

    response = client.chat.completions.create(
        model="gpt-4o-mini",  # puoi usare gpt-3.5-turbo se preferisci
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=200,
        temperature=0.4,
    )

    content = response.choices[0].message.content.strip()
    title_out = original_title
    desc_out = ""

    for line in content.splitlines():
        if line.upper().startswith("TITOLO:"):
            title_out = line.split(":", 1)[1].strip()
        elif line.upper().startswith("DESCRIZIONE:"):
            desc_out = line.split(":", 1)[1].strip()

    if desc_out == "-" or desc_out.lower() == "nessuna":
        desc_out = ""

    return title_out, desc_out


def fetch_articles_last_24h():
    """
    Legge il feed RSS e restituisce gli articoli delle ultime 24 ore
    come lista di dict: {title, link, description, pubdate}
    """
    feed = feedparser.parse(RSS_URL)
    articles = []
    now = datetime.utcnow()
    cutoff = now - timedelta(hours=HOURS_BACK)

    for entry in feed.entries:
        # alcuni feed usano 'published', altri 'pubDate'
        pub_str = getattr(entry, "published", None) or getattr(entry, "pubDate", None)
        if not pub_str:
            # se manca la data, teniamolo per sicurezza
            pub_dt = now
        else:
            try:
                pub_dt = datetime(*entry.published_parsed[:6])
            except Exception:
                pub_dt = now

        if pub_dt < cutoff:
            continue

        title = entry.title
        link = entry.link
        description = getattr(entry, "summary", "")

        articles.append(
            {
                "title": title,
                "link": link,
                "description": description,
                "pubdate": pub_dt,
            }
        )

    # Ordiniamo per data, giusto per coerenza
    articles.sort(key=lambda x: x["pubdate"])
    return articles


def build_email_html(main_articles, other_articles) -> str:
    html = []
    html.append("<html><body>")
    html.append("<h2>Articoli rilevanti</h2>")

    if not main_articles:
        html.append("<p>Nessun articolo rilevante nelle ultime 24 ore.</p>")
    else:
        html.append("<ul>")
        for art in main_articles:
            html.append("<li>")
            html.append(f"<strong>{art['title']}</strong> ")
            html.append(f'(<a href="{art["link"]}">link</a>)')
            if art.get("summary"):
                html.append(f"<br><small>{art['summary']}</small>")
            html.append("</li>")
        html.append("</ul>")

    html.append("<hr>")
    html.append("<h3>Altri articoli</h3>")

    if not other_articles:
        html.append("<p>Nessun altro articolo.</p>")
    else:
        html.append("<ul>")
        for art in other_articles:
            html.append("<li>")
            html.append(f"{art['title']} ")
            html.append(f'(<a href="{art["link"]}">link</a>)')
            html.append("</li>")
        html.append("</ul>")

    html.append("</body></html>")
    return "\n".join(html)

def send_email(subject: str, html_body: str):
    print("✉️ Preparazione email...")
    print(f"   FROM: {EMAIL_USER}")
    print(f"   TO:   {EMAIL_TO}")
    msg = MIMEText(html_body, "html", "utf-8")
    msg["Subject"] = subject
    msg["From"] = formataddr(("HDblog Digest", EMAIL_USER))
    msg["To"] = EMAIL_TO

    try:
        print("🔐 Connessione a smtp.gmail.com:465...")
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_USER, EMAIL_PASS)
            print("✅ Login SMTP riuscito, invio messaggio...")
            server.send_message(msg)
        print("✅ Email inviata con successo.")
    except Exception as e:
        print(f"❌ Errore durante l'invio dell'email: {e}")


def main():
    if not (OPENAI_API_KEY and EMAIL_USER and EMAIL_PASS and EMAIL_TO):
        print("⚠️ Variabili ambiente mancanti. Controlla OPENAI_API_KEY, EMAIL_USER, EMAIL_PASS, EMAIL_TO.")
        return

    print("🔎 Fetch RSS...")
    all_articles = fetch_articles_last_24h()
    print(f"📰 Trovati {len(all_articles)} articoli nelle ultime {HOURS_BACK} ore.")

    main_articles = []
    other_articles = []

    for art in all_articles:
        if is_low_priority(art["title"], art["description"]):
            other_articles.append({"title": art["title"], "link": art["link"]})
        else:
            try:
                clean_title, short_desc = rewrite_title_and_summary(
                    art["title"], art["description"]
                )
            except Exception as e:
                print(f"⚠️ Errore OpenAI su '{art['title']}': {e}")
                clean_title = art["title"]
                short_desc = ""

            main_articles.append(
                {
                    "title": clean_title,
                    "link": art["link"],
                    "summary": short_desc,
                }
            )

    html_body = build_email_html(main_articles, other_articles)

    today = datetime.now().strftime("%Y/%m/%d")
    subject = f"HDblog – {today} – Daily Digest"

    print(f"📨 Invio email con {len(main_articles)} articoli rilevanti e {len(other_articles)} altri articoli...")
    send_email(subject, html_body)
    print("✅ Fatto.")


if __name__ == "__main__":
    main()

