from xml.etree.ElementTree import fromstring

from amber_mcp import redact


# A realistic uiautomator node list. `text`, `content-desc` and the balance are
# data; `resource-id` (digit-bearing, like every real AmberIT list id), `bounds`
# and `class` are the selectors Task 6 exists to harvest, so they must survive
# redaction byte for byte.
XML = (
    '<node text="Rakin Uddin" content-desc="+8801712345678"/> '
    '<node text="01799887766" resource-id="com.amberit.dialer:id/item_2" '
    'bounds="[0,50][100,90]" class="android.widget.TextView"/> '
    '<node text="৳4,250.75"/>'
)

SELECTORS = (
    'resource-id="com.amberit.dialer:id/item_2"',
    'bounds="[0,50][100,90]"',
    'class="android.widget.TextView"',
)

# A PIN-shaped secret that no redaction pattern can reach: too short for the
# long-digit-run rule (which needs nine characters) and not BD-shaped. Only the
# `secrets=` loop can remove it, so this fixture cannot pass for a second reason.
UNMATCHABLE = "keypad shows 481516"


def test_known_secrets_are_replaced_by_the_secrets_layer_not_left_because_they_look_like_content():
    # Layer under test: the `secrets=` loop, alone.
    out = redact.text(UNMATCHABLE, secrets=["481516"])
    assert "481516" not in out
    assert redact.NUMBER_TOKEN in out
    assert redact.NAME_TOKEN not in out
    # The guard that makes the assertion above mean something: with no secrets
    # passed, no pattern touches this fixture, so the loop is the only way out.
    assert redact.text(UNMATCHABLE) == UNMATCHABLE


def test_a_declared_number_is_scrubbed_by_the_secrets_layer_in_xml():
    # Layer under test: the `secrets=` loop, on a captured node list.
    out = redact.text(XML, secrets=["+8801712345678"])
    assert "+8801712345678" not in out
    assert redact.NUMBER_TOKEN in out


def test_bangladeshi_mobile_shapes_are_scrubbed_by_pattern():
    # A captured list holds numbers we were never told about.
    out = redact.text('text="01799887766" text="01911223344"')
    assert "01799887766" not in out and "01911223344" not in out
    assert out.count(redact.NUMBER_TOKEN) == 2


def test_a_bare_bd_number_outside_any_attribute_is_scrubbed_by_the_pattern_layer():
    # Layer under test: the body-wide `_BD_NUMBER` substitution -- the half of the
    # pattern layer that lives outside `text=`/`content-desc=`.
    out = redact.text("dialled 01799887766 and it rang")
    assert out == f"dialled {redact.NUMBER_TOKEN} and it rang"
    assert "01799887766" not in out


def test_balance_amounts_are_scrubbed():
    out = redact.text('available balance ৳4,250.75')
    assert "4,250.75" not in out
    assert redact.BALANCE_TOKEN in out


def test_a_glyphless_balance_in_a_text_attribute_is_scrubbed_by_the_pattern_layer():
    # Layer under test: the attribute scrubber's money rule. On a real hierarchy
    # the currency glyph is usually a sibling TextView, so `text=` holds the bare
    # amount -- the balance, the most sensitive field Task 6 will capture.
    out = redact.text('<node text="4,250.75"/><node text="1,250,000.50"/>')
    assert "4,250.75" not in out and "1,250,000.50" not in out
    assert out.count(redact.BALANCE_TOKEN) == 2


def test_a_glyphless_balance_in_a_content_desc_is_scrubbed_without_reaching_a_selector():
    # Layer under test: the money rule, and the attribute scoping that keeps it
    # away from selectors.
    out = redact.text(
        '<node content-desc="1,250,000.50" bounds="[0,50][100,90]" '
        'resource-id="com.amberit.dialer:id/balance_2"/>')
    assert "1,250,000.50" not in out
    assert 'bounds="[0,50][100,90]"' in out
    assert 'resource-id="com.amberit.dialer:id/balance_2"' in out


def test_named_contacts_are_replaced_when_a_name_list_is_given():
    # Layer under test: the `secrets=` loop's name branch.
    out = redact.text(XML, secrets=["Rakin Uddin"])
    assert "Rakin Uddin" not in out
    assert redact.NAME_TOKEN in out


def test_resource_ids_survive_because_they_are_the_whole_point():
    out = redact.text(XML, secrets=["Rakin Uddin", "+8801712345678"])
    assert 'resource-id="com.amberit.dialer:id/item_2"' in out
    for selector in SELECTORS:
        assert selector in out, f"{selector} is a selector, not data"
    # ... and the number sitting in `text=` of that very node is gone.
    assert "01799887766" not in out
    assert 'text="01799887766"' not in out


def test_text_without_secrets_is_unchanged():
    plain = "com.amberit.dialer:id/dialpad_one text=\"1\""
    assert redact.text(plain) == plain


def test_an_empty_or_whitespace_input_is_not_an_error():
    assert redact.text("") == ""
    assert redact.text("   ") == "   "


# ---------------------------------------------------------------------------
# R3: `redact.xml` owns the attribute-escaping of what `redact.text` emits.
#
# The tokens are `<number>`, `<balance>`, `<name>`: valid as text, malformed
# inside an XML attribute. Escaping them is a property of XML output, not of the
# discovery harness, so it belongs here -- otherwise every future producer of
# redacted XML has to remember the same trick, and the one that forgets writes a
# hierarchy that nothing can parse.
# ---------------------------------------------------------------------------

DOCUMENT = (
    "<hierarchy>"
    '<node text="01799887766" resource-id="com.amberit.dialer:id/item_2" '
    'bounds="[0,50][100,90]" class="android.widget.TextView"/>'
    '<node text="Rakin Uddin" content-desc="+8801712345678"/>'
    '<node text="৳4,250.75"/>'
    '<node content-desc="Call" bounds="[400,600][500,700]" clickable="true"/>'
    "</hierarchy>"
)


def test_redact_xml_returns_a_document_that_elementtree_can_parse():
    out = redact.xml(DOCUMENT, secrets=["Rakin Uddin"])
    root = fromstring(out)  # raised `not well-formed` while the escaping lived downstream
    assert root.tag == "hierarchy"


def test_redact_xml_reaches_the_attribute_values_and_leaves_selectors_alone():
    out = redact.xml(DOCUMENT, secrets=["Rakin Uddin", "+8801712345678"])
    for value in (node.get("text") for node in fromstring(out).iter("node")):
        assert value not in ("01799887766", "Rakin Uddin", "+8801712345678", "৳4,250.75")
    for selector in SELECTORS:
        assert selector in out, f"{selector} is a selector, not data"


def test_redact_xml_substitutes_the_same_tokens_as_redact_text():
    tree = fromstring(redact.xml(DOCUMENT, secrets=["Rakin Uddin"]))
    node = [n for n in tree.iter("node")
            if n.get("resource-id") == "com.amberit.dialer:id/item_2"][0]
    assert node.get("text") == redact.NUMBER_TOKEN, "the token must survive parsing intact"
    names = [n.get("text") for n in tree.iter("node") if n.get("content-desc") is None]
    assert redact.BALANCE_TOKEN in names


def test_redact_xml_only_adds_characters_and_can_never_erase_a_match():
    # Escaping runs *after* redaction, so the two are the same string modulo the
    # entity references: undoing the escaping must give back `redact.text` exactly.
    # If the order were reversed the escape could break a pattern and leave a
    # number in the document.
    plain = redact.text(DOCUMENT, secrets=["Rakin Uddin", "+8801712345678"])
    escaped = redact.xml(DOCUMENT, secrets=["Rakin Uddin", "+8801712345678"])
    assert escaped.replace("&lt;", "<").replace("&gt;", ">") == plain
    assert "&lt;number&gt;" in escaped and "<number>" not in escaped


def test_redact_xml_is_unchanged_when_nothing_was_redacted():
    plain = '<hierarchy><node text="Dial" bounds="[0,0][10,10]"/></hierarchy>'
    assert redact.xml(plain) == plain == redact.text(plain)
    assert redact.xml("") == ""
