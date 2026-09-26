"""Contact form: escaping, header-injection, limits, honeypot, rate limiting.

SMTP is replaced by the ``sent_mail`` recorder in conftest — nothing is sent.
"""
import pytest

ADMIN_ADDRESS = "avigronix@gmail.com"


def contact(**overrides):
    data = {
        "full_name": "Asha Verma",
        "email": "asha@example.com",
        "phone": "+91 98765 43210",
        "company": "Example Pvt Ltd",
        "service": "Website Development",
        "message": "We need a new website.",
        "website": "",
    }
    data.update(overrides)
    return data


async def test_contact_sends_admin_and_ack(client, sent_mail):
    r = await client.post("/api/contact", json=contact())
    assert r.status_code == 200
    assert [m["To"] for m in sent_mail] == [ADMIN_ADDRESS, "asha@example.com"]
    admin_body, ack_body = sent_mail.bodies()
    assert "We need a new website." in admin_body
    assert "Asha Verma" in ack_body


async def test_html_in_fields_is_escaped_in_email_body(client, sent_mail):
    payload = contact(
        full_name="<b>Bold</b> Name",
        company='<a href="http://evil.example">click</a>',
        service="<i>svc</i>",
        message="<script>alert(1)</script> & <img src=x onerror=alert(2)>",
    )
    r = await client.post("/api/contact", json=payload)
    assert r.status_code == 200
    admin_body, ack_body = sent_mail.bodies()
    for raw in ["<script>", "<img", "<b>Bold</b>", '<a href="http://evil.example">', "<i>svc</i>"]:
        assert raw not in admin_body
        assert raw not in ack_body
    assert "&lt;script&gt;alert(1)&lt;/script&gt; &amp; &lt;img" in admin_body
    assert "&lt;b&gt;Bold&lt;/b&gt; Name" in ack_body


async def test_ack_email_does_not_reflect_user_message(client, sent_mail):
    r = await client.post(
        "/api/contact",
        json=contact(message="UNIQUE-MESSAGE-TOKEN visit http://phish.example", phone="555-UNIQUE-PHONE",
                     service="UNIQUE-SERVICE"),
    )
    assert r.status_code == 200
    ack = [m for m in sent_mail if m["To"] == "asha@example.com"][0]
    ack_body = sent_mail.bodies()[sent_mail.index(ack)]
    for token in ["UNIQUE-MESSAGE-TOKEN", "phish.example", "555-UNIQUE-PHONE", "UNIQUE-SERVICE"]:
        assert token not in ack_body


@pytest.mark.parametrize("name", ["Evil\r\nBcc: victim@example.com", "Evil\nBcc: victim@example.com", "Evil\rX: y"])
async def test_crlf_in_name_cannot_inject_headers(client, sent_mail, name):
    r = await client.post("/api/contact", json=contact(full_name=name))
    assert r.status_code in (200, 422)
    for msg in sent_mail:
        assert msg["Bcc"] is None
        assert msg["X"] is None
        assert "\r" not in msg["Subject"] and "\n" not in msg["Subject"]
        # the serialized header block must not contain an injected header line
        # (the text may legitimately appear, escaped, inside the HTML body)
        header_block = msg.as_string().split("\n\n", 1)[0]
        assert "Bcc:" not in header_block
        assert "\nX:" not in header_block


@pytest.mark.parametrize(
    "field,length",
    [("full_name", 201), ("phone", 21), ("company", 201), ("service", 101), ("message", 5001), ("website", 201)],
)
async def test_over_length_fields_rejected(client, sent_mail, field, length):
    r = await client.post("/api/contact", json=contact(**{field: "x" * length}))
    assert r.status_code == 422
    assert sent_mail == []


async def test_invalid_email_rejected(client, sent_mail):
    r = await client.post("/api/contact", json=contact(email="not-an-email"))
    assert r.status_code == 422
    assert sent_mail == []


async def test_missing_required_fields_rejected(client, sent_mail):
    r = await client.post("/api/contact", json={"email": "a@example.com"})
    assert r.status_code == 422
    assert sent_mail == []


async def test_honeypot_filled_pretends_success_but_sends_nothing(client, sent_mail):
    r = await client.post("/api/contact", json=contact(website="http://spam.example"))
    assert r.status_code == 200
    assert r.json()["msg"] == "Message sent successfully"
    assert sent_mail == []


async def test_contact_rate_limited_after_five_per_window(client, sent_mail):
    for _ in range(5):
        assert (await client.post("/api/contact", json=contact())).status_code == 200
    r = await client.post("/api/contact", json=contact())
    assert r.status_code == 429
    assert len(sent_mail) == 10  # 5 accepted submissions x (admin + ack)


async def test_smtp_failure_returns_error_not_traceback(client, monkeypatch):
    import main

    class FailingSMTP:
        def __init__(self, *a, **kw):
            raise OSError("SMTP-INTERNAL-DETAIL")

    monkeypatch.setattr(main.smtplib, "SMTP", FailingSMTP)
    r = await client.post("/api/contact", json=contact())
    assert r.status_code == 500
    assert "SMTP-INTERNAL-DETAIL" not in r.text
