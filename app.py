"""
AALC — Application d'Analyse des Leçons de Conduite
Projet APPEC — Validation de l'Hypothèse 3
"""
import streamlit as st
import re, io, json
from datetime import datetime
from collections import Counter

st.set_page_config(page_title="AALC — Projet APPEC", page_icon="🚗", layout="wide")

# ── CODEBOOK ────────────────────────────────────────────────────────────────

PHASES = ["Introduction", "Corps de la leçon (Roulage)", "Bilan final / Arrêt"]
ENSEIGNANTS = [f"E{i}" for i in range(1, 11)]
DISPOSITIF = "Dispositif Hybride"
C07_TYPES = [
    ("Perceptif",   "Questions fermées de prélèvement d'indices"),
    ("Evaluation",  "Questions de validation des connaissances du Code"),
    ("Guidage",     "Questions semi-ouvertes de planification de l'action"),
    ("Reflexif",    "Questions ouvertes de maïeutique, retour réflexif"),
]
POLES = {
    "P1": {"titre": "Pôle 1 — Contrôleur",      "color": "#a8332b"},
    "P2": {"titre": "Pôle 2 — Transmetteur",     "color": "#1f5e8c"},
    "P3": {"titre": "Pôle 3 — Accompagnateur",   "color": "#1d7a52"},
}
COMPTEURS = [
    ("C01", "Corrections",         "P1", False, "Corrections d'erreurs en temps réel"),
    ("C02", "Injonctions",         "P1", False, "Injonctions et impératifs directs"),
    ("C03", "Sur-étayage",         "P1", True,  "⚠️ Codé depuis la vidéo uniquement"),
    ("C04", "Permis",              "P1", False, "Références aux critères de l'examen"),
    ("C05_Brut",     "Objectif brut",      "P2", False, "Simple annonce de l'objectif"),
    ("C05_Scenarise","Objectif scénarisé", "P2", False, "Avec connecteurs séquentiels (d'abord, ensuite, puis...)"),
    ("C10", "Évaluations",         "P2", False, "Évaluation formative"),
    ("C11", "Explications",        "P2", False, "Comment → pourquoi → risques (connecteurs logiques)"),
    ("C12", "Démonstrations",      "P2", True,  "⚠️ Codé depuis la vidéo uniquement"),
    ("C06", "Arrêts pédagogiques", "P3", False, "Arrêts du véhicule à visée pédagogique"),
    ("C07", "Questionnement",      "P3", False, "Question clinique Boccara (avec sous-type)"),
    ("C08", "Outils",              "P3", False, "Support pédagogique physique ou numérique"),
    ("C09", "Conduite commentée",  "P3", False, "L'élève verbalise ses propres actions"),
    ("C13", "Leçon collective",    "P3", False, "Élève sur la banquette arrière"),
]

# ── STATE ───────────────────────────────────────────────────────────────────

def init_state():
    if "teachers" not in st.session_state:
        st.session_state.teachers = {
            e: {"transcript": "", "noeuds": [], "prefix_ens": ""}
            for e in ENSEIGNANTS
        }
    if "prefix_elv" not in st.session_state:
        st.session_state.prefix_elv = "Élève"
    if "api_key" not in st.session_state:
        st.session_state.api_key = ""
    if "nid" not in st.session_state:
        st.session_state.nid = 0

def add_noeud(eid, citation, code, phase, c07type=None, source="✋"):
    st.session_state.nid += 1
    d = st.session_state.teachers[eid]
    d["noeuds"].append({
        "id": st.session_state.nid,
        "u": len(d["noeuds"]) + 1,
        "citation": citation,
        "code": code,
        "phase": phase,
        "c07type": c07type,
        "source": source,
    })

def del_noeud(eid, nid):
    d = st.session_state.teachers[eid]
    d["noeuds"] = [n for n in d["noeuds"] if n["id"] != nid]

# ── PARSING ──────────────────────────────────────────────────────────────────

def parse_dv(transcript, prefix_ens, prefix_elv):
    pe = (prefix_ens or "").strip()
    pl = (prefix_elv or "").strip()
    def mk(p): return re.compile(r"^(?:L\d+\s+)?[-–\s]*\s*" + re.escape(p) + r"[^:]*:\s*", re.I) if p else None
    reE, reL = mk(pe), mk(pl)
    ew = lw = ig = 0
    for line in (transcript or "").split("\n"):
        if not line.strip(): continue
        cw = lambda s: len(s.split()) if s.strip() else 0
        if reE and reE.match(line): ew += cw(reE.sub("", line))
        elif reL and reL.match(line): lw += cw(reL.sub("", line))
        else: ig += 1
    tot = ew + lw
    return {"ew": ew, "lw": lw, "ig": ig,
            "pe": round(1000*ew/tot)/10 if tot else 0,
            "pl": round(1000*lw/tot)/10 if tot else 0}

def detect_prefix(text):
    for line in text.split("\n")[:30]:
        # Format L01 E1 :
        m = re.match(r"^L\d+\s+(.+?)\s*:", line)
        if m:
            l = m.group(1).strip()
            if not re.search(r"élève|eleve", l, re.I):
                return l
        # Format - Enseignante : ou • Enseignante :
        m2 = re.match(r"^[-–•]\s+(.+?)\s*:", line)
        if m2:
            l = m2.group(1).strip()
            if not re.search(r"élève|eleve", l, re.I) and len(l) < 40:
                return l
    return None

def extract_odt(data: bytes) -> str:
    import zipfile, xml.etree.ElementTree as ET
    NS = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        xml = z.read("content.xml").decode("utf-8")
    root = ET.fromstring(xml)
    # Récupère chaque paragraphe séparément (conserve la structure)
    paras = ["".join(e.itertext()).strip()
             for e in root.iter(f"{{{NS}}}p")]
    paras = [p for p in paras if p]
    raw = " ".join(paras)
    # Format 1 : L01 E1 : texte
    if re.search(r"L\d+\s+\w", raw):
        segs = re.split(r"(?=L\d+\s)", raw)
        lines = [re.sub(r"([^\s])\s*:\s*(?=\S)", r"\1 : ", s.strip())
                 for s in segs if s.strip()]
        return "\n".join(lines)
    # Format 2 : paragraphes déjà séparés commençant par "Enseignante :" ou "Elève :"
    speaker_pattern = re.compile(
        r"^[-–•]?\s*(Enseignant[e]?|Élève|Elève|E\d+)\s*:", re.I)
    if any(speaker_pattern.match(p) for p in paras):
        return "\n".join(paras)
    # Format 3 : tout en un bloc — redécoupe sur les préfixes "Mot(s) :"
    speaker_re = re.compile(r"(?<!\w)(Enseignant[e]?|Élève|Elève|E\d+)\s*:\s*")
    parts = speaker_re.split(raw)
    lines = []
    i = 1
    while i < len(parts) - 1:
        speaker = parts[i].strip()
        content = re.sub(r"\s+", " ", parts[i+1].strip()) if i+1 < len(parts) else ""
        if content:
            lines.append(f"{speaker} : {content}")
        i += 2
    if lines:
        return "\n".join(lines)
    # Fallback
    return "\n".join(paras)

def extract_docx(data: bytes) -> str:
    from docx import Document
    doc = Document(io.BytesIO(data))
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())

# ── CALCULS ──────────────────────────────────────────────────────────────────

def counts_by_code(noeuds):
    c = Counter()
    for n in noeuds:
        c[n["code"]] += 1
        if n["code"] == "C07" and n.get("c07type"):
            c["C07_" + n["c07type"]] += 1
    return c

def synthese(data, prefix_ens, prefix_elv):
    dv = parse_dv(data["transcript"], prefix_ens, prefix_elv)
    ns = data["noeuds"]
    c = counts_by_code(ns)
    g = lambda k: c.get(k, 0)
    total = len(ns)
    pole_codes = {pk: [ct[0] for ct in COMPTEURS if ct[2] == pk] for pk in ["P1","P2","P3"]}
    pv = {pk: sum(1 for n in ns if n["code"] in pole_codes[pk]) for pk in ["P1","P2","P3"]}
    directifs = g("C01") + g("C02") + g("C03")
    denom_et = g("C02") + g("C11")
    denom_boc = g("C07_Perceptif") + g("C07_Evaluation") + g("C07_Guidage")
    return {
        "dv": dv, "total": total, "counts": c, "pv": pv,
        "ratio_dir": round(directifs/total, 3) if total else None,
        "etayage": round((g("C07_Reflexif")+g("C09"))/denom_et, 3) if denom_et else None,
        "ouverture": round(g("C07_Reflexif")/denom_boc, 3) if denom_boc else None,
    }

# ── AI ───────────────────────────────────────────────────────────────────────

def appeler_claude(system_prompt, user_prompt, max_tokens=5000):
    import requests, time
    api_key = st.session_state.api_key.strip()
    if not api_key:
        st.error("⚠️ Clé API Anthropic manquante — renseignez-la dans la barre latérale.")
        st.stop()
    payload = {
        "model": "claude-sonnet-4-6",
        "max_tokens": max_tokens,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}]
    }
    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01"
    }
    last_error = None
    for tentative in range(3):
        try:
            resp = requests.post(
                "https://api.anthropic.com/v1/messages",
                json=payload, headers=headers, timeout=120
            )
            if resp.status_code != 200:
                msg = f"Erreur API {resp.status_code} : {resp.text[:500]}"
                st.error(f"❌ {msg}")
                st.stop()
            data = resp.json()
            return next((b["text"] for b in data.get("content", []) if b["type"] == "text"), "")
        except requests.exceptions.Timeout:
            last_error = "Timeout (délai dépassé)"
            if tentative < 2:
                time.sleep(5)
                continue
        except Exception as e:
            last_error = str(e)
            if tentative < 2:
                time.sleep(3)
                continue
    st.error(f"❌ Échec après 3 tentatives : {last_error}")
    st.stop()

SYSTEM_CODAGE = """Tu es expert en analyse qualitative NVivo des leçons de conduite (Bucheton 2009, Pratt 1998, Boccara).
Extrais des Unités de Sens et classe-les:
P1: C01(Corrections) C02(Injonctions) C04(Permis) — NE PAS coder C03/C12 (vidéo uniquement)
P2: C05_Brut(objectif simple) C05_Scenarise(connecteurs: d'abord/ensuite/puis/dans un premier temps/premièrement...) C10(éval. formative) C11(comment+pourquoi+risques via connecteurs logiques)
P3: C06(Arrêts) C07(question Boccara — c07type obligatoire: Perceptif/Evaluation/Guidage/Reflexif) C08(Outils) C09(Conduite commentée) C13(Leçon collective)
Phase: "Introduction" | "Corps de la leçon (Roulage)" | "Bilan final / Arrêt"
Réponds UNIQUEMENT JSON valide: [{"citation":"texte exact","code":"C01","phase":"Introduction","c07type":null},...]"""

SYSTEM_THESE = """Co-auteur d'une thèse SEF (Rouen) sur les postures d'étayage (Bucheton & Soulé 2009, Pratt 1998) d'enseignants de la conduite en formation hybride (cadre Boccara, Vygotski, Bruner).
Méthode: études de cas — la variabilité interindividuelle est une ressource analytique, jamais un problème.
Registre académique français, 3e personne, nuance épistémique (semble indiquer, tend à suggérer).
N'invente aucune donnée. Structure avec titres Markdown (## puis ###)."""

# ── EXPORT WORD ──────────────────────────────────────────────────────────────

def build_doc_html(body, title):
    return f"""<html xmlns:o='urn:schemas-microsoft-com:office:office'
xmlns:w='urn:schemas-microsoft-com:office:word' xmlns='http://www.w3.org/TR/REC-html40'>
<head><meta charset="utf-8"><title>{title}</title>
<style>body{{font-family:Calibri,Arial;font-size:11pt;line-height:1.6;}}
h1{{font-size:18pt;text-align:center;}} h2{{font-size:14pt;color:#1f5e8c;border-bottom:1pt solid #ccc;margin-top:14pt;}}
h3{{font-size:12pt;color:#333;}} p{{margin:5pt 0;}} em{{font-style:italic;}}
table{{border-collapse:collapse;width:100%;margin:8pt 0;}} td,th{{border:1pt solid #999;padding:4pt 7pt;font-size:10pt;}}
th{{background:#f0f0f0;}} ul{{margin:4pt 0 4pt 16pt;}} li{{margin:2pt 0;}}
</style></head><body>{body}</body></html>"""

def md_to_html(md):
    lines, html, liste = (md or "").split("\n"), "", False
    def fmt(s):
        s = s.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
        s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"\*(.+?)\*", r"<em>\1</em>", s)
        return s
    for l in lines:
        t = l.strip()
        if not t:
            if liste: html += "</ul>"; liste = False
            continue
        if t.startswith("### "): html += f"<h3>{fmt(t[4:])}</h3>"
        elif t.startswith("## "): html += f"<h2>{fmt(t[3:])}</h2>"
        elif t.startswith("# "): html += f"<h1>{fmt(t[2:])}</h1>"
        elif t.startswith("- ") or t.startswith("* "):
            if not liste: html += "<ul>"; liste = True
            html += f"<li>{fmt(t[2:])}</li>"
        else:
            if liste: html += "</ul>"; liste = False
            html += f"<p>{fmt(t)}</p>"
    if liste: html += "</ul>"
    return html

# ── UI ───────────────────────────────────────────────────────────────────────

init_state()

with st.sidebar:
    st.title("🚗 AALC — Projet APPEC")
    at = st.selectbox("Enseignant actif", ENSEIGNANTS)
    ph = st.selectbox("Phase active", PHASES)
    st.session_state.prefix_elv = st.text_input("Préfixe élève", st.session_state.prefix_elv)
    st.session_state.api_key = st.text_input("Clé API Anthropic", st.session_state.api_key, type="password")
    st.divider()
    st.caption("Échantillon N=10")
    for e in ENSEIGNANTS:
        d = st.session_state.teachers[e]
        dve = parse_dv(d["transcript"], d.get("prefix_ens","") or "Enseignant", st.session_state.prefix_elv)
        has = "✅" if d["transcript"].strip() else "⬜"
        mark = "🟢" if e == at else "⚪"
        st.caption(f"{mark} {e} {has} {dve['ew']}/{dve['lw']}m · {len(d['noeuds'])}US")
    if st.button("🗑 Réinitialiser tout", type="secondary"):
        if st.session_state.get("confirm_reset"):
            st.session_state.teachers = {e: {"transcript":"","noeuds":[],"prefix_ens":""} for e in ENSEIGNANTS}
            st.session_state.nid = 0
            del st.session_state["confirm_reset"]
            st.rerun()
        else:
            st.session_state.confirm_reset = True
            st.warning("Cliquez encore pour confirmer")

td = st.session_state.teachers[at]
pe = td.get("prefix_ens","") or "Enseignant"

tab_cod, tab_ind, tab_long, tab_exp = st.tabs(["🖊 Codage", "👤 Individuel", "📊 Longitudinal", "📄 Export"])

# ════════════════════════════════════════════════════════════════
# TAB CODAGE
# ════════════════════════════════════════════════════════════════
with tab_cod:
    col_import, col_pfx = st.columns([3,1])
    with col_import:
        f = st.file_uploader(f"Importer la transcription de {at} (.odt ou .docx)", type=["odt","docx","doc"])
    with col_pfx:
        new_pe = st.text_input("Préfixe enseignant", pe)
        if new_pe != pe:
            td["prefix_ens"] = new_pe
            pe = new_pe

    if f:
        try:
            raw = f.read()
            ext = f.name.lower().split(".")[-1]
            text = extract_odt(raw) if ext == "odt" else extract_docx(raw)
            det = detect_prefix(text)
            td["transcript"] = text
            if det: td["prefix_ens"] = det; pe = det
            st.success(f"✅ {f.name} chargé{f' — préfixe détecté : «{det}»' if det else ''}")
        except Exception as ex:
            st.error(f"❌ {ex}")

    dv = parse_dv(td["transcript"], pe, st.session_state.prefix_elv)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Mots enseignant", dv["ew"])
    c2.metric("Mots élève", dv["lw"])
    c3.metric("Densité Ens./Élève", f"{dv['pe']}% / {dv['pl']}%" if dv["ew"]+dv["lw"] else "—")
    c4.metric("Lignes non attribuées", dv["ig"])

    left, right = st.columns(2)
    with left:
        td["transcript"] = st.text_area("Transcription", td["transcript"], height=250)
        st.caption(f"Phase active : **{ph}**")

        if st.button("🤖 Analyser automatiquement (IA)", type="primary"):
            if not td["transcript"].strip():
                st.warning("Transcription vide.")
            else:
                lignes = td["transcript"].split("\n")
                # Découpe en segments de 40 lignes max pour éviter les timeouts
                taille_segment = 40
                segments = [lignes[i:i+taille_segment] for i in range(0, len(lignes), taille_segment)]
                nb_segments = len(segments)
                toutes_unites = []
                erreur = False
                for idx_seg, segment in enumerate(segments):
                    with st.spinner(f"Analyse IA en cours... (partie {idx_seg+1}/{nb_segments})"):
                        texte_seg = "\n".join(segment)
                        result = appeler_claude(
                            SYSTEM_CODAGE,
                            f'Préfixe ens:"{pe}" élève:"{st.session_state.prefix_elv}".\n{texte_seg}',
                            max_tokens=8000
                        )
                    if result:
                        try:
                            cleaned = re.sub(r"^```json|^```|```$", "", result.strip()).strip()
                            start = cleaned.find("[")
                            if start == -1: continue
                            cleaned = cleaned[start:]
                            try:
                                unites = json.loads(cleaned)
                            except json.JSONDecodeError:
                                last_complete = cleaned.rfind("},")
                                if last_complete == -1: last_complete = cleaned.rfind("}")
                                if last_complete > 0:
                                    cleaned = cleaned[:last_complete+1] + "]"
                                    unites = json.loads(cleaned)
                                else:
                                    continue
                            toutes_unites.extend(unites)
                        except Exception as ex:
                            st.error(f"❌ Erreur de parsing (partie {idx_seg+1}) : {ex}")
                            erreur = True
                            break

                if not erreur and toutes_unites:
                    td["noeuds"] = [n for n in td["noeuds"] if n["source"] != "🤖"]
                    for u in toutes_unites:
                        if not u.get("citation") or not u.get("code"): continue
                        codes = [ct[0] for ct in COMPTEURS]
                        if u["code"] not in codes: continue
                        if PHASES.count(u.get("phase","")) == 0: continue
                        vid = next((ct[3] for ct in COMPTEURS if ct[0]==u["code"]), False)
                        if vid: continue
                        add_noeud(at, u["citation"], u["code"], u["phase"], u.get("c07type"), "🤖")
                    st.success(f"✅ {len(toutes_unites)} Unités de Sens analysées ({nb_segments} partie(s)).")
                    st.rerun()

    with right:
        st.subheader(f"Compteurs cliniques — {at} ({len(td['noeuds'])} US)")
        for pk, pinfo in POLES.items():
            with st.expander(pinfo["titre"], expanded=True):
                for code, label, pole, video, desc in COMPTEURS:
                    if pole != pk: continue
                    nb = sum(1 for n in td["noeuds"] if n["code"] == code)
                    vid_badge = " 📹" if video else ""
                    if code == "C07":
                        st.markdown(f"**{code} — {label}** `{nb}`{vid_badge}")
                        c07_sel = st.radio("Sous-type", [t[0] for t in C07_TYPES],
                                          format_func=lambda k: k,
                                          horizontal=True, key=f"c7_{at}")
                        citation = st.text_input("Citation", key=f"cit_c07_{at}", label_visibility="collapsed",
                                                 placeholder="Collez la citation ici")
                        if st.button(f"➕ Encoder C07 ({c07_sel})", key=f"btn_c07_{at}"):
                            if citation.strip():
                                add_noeud(at, citation.strip(), "C07", ph, c07_sel, "✋")
                                st.rerun()
                    else:
                        col_btn, col_lbl = st.columns([1, 5])
                        col_lbl.caption(f"**{code}** — {label}{vid_badge} `{nb}`")
                        if col_btn.button("➕", key=f"btn_{code}_{at}", disabled=video):
                            cit = st.session_state.get(f"cit_{code}_{at}", "")
                            if cit.strip():
                                add_noeud(at, cit.strip(), code, ph, None, "✋")
                                st.rerun()
                        st.text_input("Citation", key=f"cit_{code}_{at}", label_visibility="collapsed",
                                      placeholder=f"Citation pour {code}")

    st.divider()
    st.subheader(f"Unités de Sens codées — {at}")
    if not td["noeuds"]:
        st.caption("Aucune US codée.")
    else:
        for n in reversed(td["noeuds"]):
            comp = next((ct for ct in COMPTEURS if ct[0]==n["code"]), None)
            label = comp[1] if comp else n["code"]
            sub = f" ({n['c07type']})" if n.get("c07type") else ""
            col_t, col_d = st.columns([10, 1])
            col_t.markdown(f"`US{n['u']}` **{n['code']}{sub} — {label}** · _{n['phase']}_ {n['source']}  \n*« {n['citation']} »*")
            if col_d.button("🗑", key=f"del_{n['id']}"):
                del_noeud(at, n["id"])
                st.rerun()

    st.divider()
    st.subheader("Vue d'ensemble des verbatims (N=10)")
    rows = []
    for e in ENSEIGNANTS:
        d = st.session_state.teachers[e]
        dve = parse_dv(d["transcript"], d.get("prefix_ens","") or "Enseignant", st.session_state.prefix_elv)
        rows.append({
            "Cas": ("🟢 " if e==at else "") + e,
            "Transcription": "✅" if d["transcript"].strip() else "⬜",
            "Mots Ens.": dve["ew"] or "—",
            "Mots Élève": dve["lw"] or "—",
            "Densité Ens.": f"{dve['pe']}%" if dve["ew"]+dve["lw"] else "—",
            "Densité Él.": f"{dve['pl']}%" if dve["ew"]+dve["lw"] else "—",
            "US codées": len(d["noeuds"]),
        })
    total_ew = sum(parse_dv(st.session_state.teachers[e]["transcript"], st.session_state.teachers[e].get("prefix_ens","") or "Enseignant", st.session_state.prefix_elv)["ew"] for e in ENSEIGNANTS)
    total_lw = sum(parse_dv(st.session_state.teachers[e]["transcript"], st.session_state.teachers[e].get("prefix_ens","") or "Enseignant", st.session_state.prefix_elv)["lw"] for e in ENSEIGNANTS)
    total_us = sum(len(st.session_state.teachers[e]["noeuds"]) for e in ENSEIGNANTS)
    tt = total_ew + total_lw
    rows.append({
        "Cas": "**CORPUS**", "Transcription": "",
        "Mots Ens.": total_ew, "Mots Élève": total_lw,
        "Densité Ens.": f"{round(1000*total_ew/tt)/10}%" if tt else "—",
        "Densité Él.": f"{round(1000*total_lw/tt)/10}%" if tt else "—",
        "US codées": total_us,
    })
    import pandas as pd
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

# ════════════════════════════════════════════════════════════════
# TAB INDIVIDUEL
# ════════════════════════════════════════════════════════════════
with tab_ind:
    import plotly.graph_objects as go
    d = st.session_state.teachers[at]
    s = synthese(d, pe, st.session_state.prefix_elv)
    m1,m2,m3,m4,m5,m6,m7 = st.columns(7)
    m1.metric("Mots Ens.", s["dv"]["ew"])
    m2.metric("Mots Élève", s["dv"]["lw"])
    m3.metric("Densité Ens.", f"{s['dv']['pe']}%" if s["dv"]["ew"]+s["dv"]["lw"] else "—")
    m4.metric("US totales", s["total"])
    m5.metric("P1", s["pv"]["P1"])
    m6.metric("P2", s["pv"]["P2"])
    m7.metric("P3", s["pv"]["P3"])

    all_codes = [ct[0] for ct in COMPTEURS if not ct[3]] + ["C07_"+t[0] for t in C07_TYPES]
    vals = [s["counts"].get(k, 0) for k in all_codes]
    colors = ["#a8332b" if k in ["C01","C02","C03","C04"] else "#1f5e8c" if k in ["C05_Brut","C05_Scenarise","C10","C11","C12"] else "#1d7a52" for k in all_codes]
    fig = go.Figure(go.Bar(x=vals, y=all_codes, orientation="h", marker_color=colors))
    fig.update_layout(title=f"Volume par composante — {at}", height=420, margin=dict(l=10,r=10,t=40,b=10))
    st.plotly_chart(fig, use_container_width=True)

    for pk, pinfo in POLES.items():
        pole_codes = [ct[0] for ct in COMPTEURS if ct[2]==pk]
        pn = [n for n in d["noeuds"] if n["code"] in pole_codes]
        if not pn: continue
        with st.expander(f"{pinfo['titre']} — {len(pn)} US", expanded=False):
            for code, label, pole, vid, desc in COMPTEURS:
                if pole != pk: continue
                un = [n for n in pn if n["code"]==code]
                if not un: continue
                st.markdown(f"**{code} — {label}** ({len(un)} US)")
                for n in un:
                    sub = f" ({n['c07type']})" if n.get("c07type") else ""
                    st.markdown(f"- US{n['u']} [{n['phase']}] {n['source']} *« {n['citation']} »*")

# ════════════════════════════════════════════════════════════════
# TAB LONGITUDINAL
# ════════════════════════════════════════════════════════════════
with tab_long:
    all_s = []
    for e in ENSEIGNANTS:
        d2 = st.session_state.teachers[e]
        s2 = synthese(d2, d2.get("prefix_ens","") or "Enseignant", st.session_state.prefix_elv)
        s2["id"] = e
        all_s.append(s2)
    actifs = [s for s in all_s if s["total"]>0]

    a1, a2 = st.columns(2)
    a1.metric("Cas codés", f"{len(actifs)}/10")
    a2.metric("US totales corpus", sum(s["total"] for s in all_s))

    if actifs:
        fig2 = go.Figure()
        for pk, pinfo in POLES.items():
            fig2.add_trace(go.Bar(name=pinfo["titre"], x=[s["id"] for s in actifs], y=[s["pv"][pk] for s in actifs], marker_color=pinfo["color"]))
        fig2.update_layout(barmode="group", title="Volumes par Pôle par Enseignant", height=300)
        st.plotly_chart(fig2, use_container_width=True)

        all_n = [n for e in ENSEIGNANTS for n in st.session_state.teachers[e]["noeuds"]]
        boc = {t[0]: sum(1 for n in all_n if n["code"]=="C07" and n.get("c07type")==t[0]) for t in C07_TYPES}
        if sum(boc.values()):
            fig3 = go.Figure(go.Pie(labels=list(boc.keys()), values=list(boc.values()), hole=.45,
                                     marker_colors=["#85C1E9","#F8C471","#82E0AA","#27AE60"]))
            fig3.update_layout(title="Spectre du Questionnement Clinique (Boccara)", height=280)
            st.plotly_chart(fig3, use_container_width=True)

# ════════════════════════════════════════════════════════════════
# TAB EXPORT
# ════════════════════════════════════════════════════════════════
with tab_exp:
    actifs_ids = [e for e in ENSEIGNANTS if st.session_state.teachers[e]["noeuds"]]
    st.info(f"{len(actifs_ids)} enseignant(s) avec US codées sur 10.")

    if not actifs_ids:
        st.warning("Codez au moins une US pour au moins un enseignant.")
    else:
        if st.button("🤖 Générer la synthèse complète (.doc)", type="primary"):
            progress = st.progress(0, "Démarrage...")
            body = f"""<h1>Synthèse Complète du Corpus — Projet APPEC</h1>
<p style="text-align:center;color:#666;font-size:9pt;">{DISPOSITIF} · {len(actifs_ids)} cas · {datetime.now().strftime('%d/%m/%Y %H:%M')}</p>
<h1>Partie I — Traitement Individuel (Études de Cas)</h1>"""

            for i, eid in enumerate(actifs_ids):
                progress.progress((i+1)/(len(actifs_ids)+1), f"Analyse de {eid} ({i+1}/{len(actifs_ids)})...")
                d3 = st.session_state.teachers[eid]
                pe3 = d3.get("prefix_ens","") or "Enseignant"
                s3 = synthese(d3, pe3, st.session_state.prefix_elv)
                body += f"""<h2>Cas {eid}</h2>
<table><tr><th>Mots Ens.</th><th>Mots Élève</th><th>Densité Ens.</th><th>Densité Él.</th><th>US</th><th>P1</th><th>P2</th><th>P3</th></tr>
<tr><td>{s3['dv']['ew']}</td><td>{s3['dv']['lw']}</td><td>{s3['dv']['pe']}%</td><td>{s3['dv']['pl']}%</td><td>{s3['total']}</td><td>{s3['pv']['P1']}</td><td>{s3['pv']['P2']}</td><td>{s3['pv']['P3']}</td></tr></table>"""
                cat = "\n".join(f"[US{n['u']}|{n['code']}{('('+n['c07type']+')') if n.get('c07type') else ''}|{n['phase']}] «{n['citation']}»" for n in d3["noeuds"])
                # Sélectionne les citations les plus représentatives (max 3 par pôle) pour alléger le prompt
                us_par_pole = {}
                for n in d3["noeuds"]:
                    pk = next((ct[2] for ct in COMPTEURS if ct[0]==n["code"]), None)
                    if pk:
                        us_par_pole.setdefault(pk, []).append(n)
                citations_selectionnees = []
                for pk in ["P1","P2","P3"]:
                    for n in us_par_pole.get(pk, [])[:4]:
                        citations_selectionnees.append(f"[{n['code']}{('('+n['c07type']+')') if n.get('c07type') else ''}|{n['phase']}] «{n['citation']}»")
                prompt = f"""Rédige une analyse de cas CONCISE (environ 3-4 pages A4) pour l'enseignant {eid}.
Structure STRICTE — chaque section : 1 paragraphe dense maximum :
## 1. Présentation du cas (5 lignes)
## 2. Densité verbale et espace de parole (5 lignes)
## 3. Profil de postures d'étayage — Contrôleur / Transmetteur / Accompagnateur (3 paragraphes courts)
## 4. Composantes cliniques saillantes et citations (1 paragraphe par pôle, 1-2 citations maximum)
## 5. Interprétation théorique au regard de Pratt / Bucheton / Boccara (1 paragraphe)

Données : P1={s3['pv']['P1']} US | P2={s3['pv']['P2']} US | P3={s3['pv']['P3']} US | Total={s3['total']} US
Densité verbale : Ens.={s3['dv']['pe']}% ({s3['dv']['ew']} mots) / Élève={s3['dv']['pl']}% ({s3['dv']['lw']} mots)
Activations par code : {dict(s3['counts'])}

Citations représentatives (extrait) :
{chr(10).join(citations_selectionnees)}

IMPORTANT : sois dense et synthétique. Pas de listes à puces. Prose académique continue. 3-4 pages maximum."""
                prose = appeler_claude(SYSTEM_THESE, prompt, 3000)
                if prose:
                    body += md_to_html(prose)
                body += "<h3>Annexe — Catalogue des US</h3><ul>"
                for n in d3["noeuds"]:
                    comp_label = next((ct[1] for ct in COMPTEURS if ct[0]==n["code"]), n["code"])
                    sub = f" ({n['c07type']})" if n.get("c07type") else ""
                    body += f"<li><strong>[US{n['u']} · {n['code']}{sub} — {comp_label} · {n['phase']}]</strong> <em>« {n['citation']} »</em></li>"
                body += "</ul>"

            progress.progress(1.0, "Synthèse transversale...")
            body += "<h1>Partie II — Traitement Collectif (Synthèse Transversale)</h1>"
            body += """<h2>Densité verbale du corpus</h2>
<table><tr><th>Cas</th><th>Mots Ens.</th><th>Mots Élève</th><th>Densité Ens.</th><th>Densité Él.</th><th>US</th><th>P1</th><th>P2</th><th>P3</th></tr>"""
            tw=tl=tu=0
            for e in ENSEIGNANTS:
                d4 = st.session_state.teachers[e]
                dve = parse_dv(d4["transcript"], d4.get("prefix_ens","") or "Enseignant", st.session_state.prefix_elv)
                s4 = synthese(d4, d4.get("prefix_ens","") or "Enseignant", st.session_state.prefix_elv)
                tw+=dve["ew"]; tl+=dve["lw"]; tu+=len(d4["noeuds"])
                body += f"<tr><td>{e}</td><td>{dve['ew'] or '—'}</td><td>{dve['lw'] or '—'}</td><td>{str(dve['pe'])+'%' if dve['ew']+dve['lw'] else '—'}</td><td>{str(dve['pl'])+'%' if dve['ew']+dve['lw'] else '—'}</td><td>{len(d4['noeuds'])}</td><td>{s4['pv']['P1']}</td><td>{s4['pv']['P2']}</td><td>{s4['pv']['P3']}</td></tr>"
            tt=tw+tl
            body += f"<tr style='font-weight:bold'><td>TOTAL</td><td>{tw}</td><td>{tl}</td><td>{str(round(1000*tw/tt)/10)+'%' if tt else '—'}</td><td>{str(round(1000*tl/tt)/10)+'%' if tt else '—'}</td><td>{tu}</td><td>—</td><td>—</td><td>—</td></tr></table>"

            det = " | ".join(f"{e}: P1={synthese(st.session_state.teachers[e], st.session_state.teachers[e].get('prefix_ens','') or 'Enseignant', st.session_state.prefix_elv)['pv']['P1']} P2={synthese(st.session_state.teachers[e], st.session_state.teachers[e].get('prefix_ens','') or 'Enseignant', st.session_state.prefix_elv)['pv']['P2']} P3={synthese(st.session_state.teachers[e], st.session_state.teachers[e].get('prefix_ens','') or 'Enseignant', st.session_state.prefix_elv)['pv']['P3']} US={len(st.session_state.teachers[e]['noeuds'])}" for e in ENSEIGNANTS if st.session_state.teachers[e]["noeuds"])
            prose2 = appeler_claude(SYSTEM_THESE, f"""Rédige une synthèse transversale CONCISE (environ 6-8 pages A4) du corpus complet (N={len(ENSEIGNANTS)}, Dispositif Hybride, études de cas).
Structure STRICTE — prose académique dense, pas de listes à puces :
## 1. Vue d'ensemble du corpus (1 paragraphe)
## 2. Tendances posturales dominantes à l'échelle du groupe (2 paragraphes)
## 3. Composantes cliniques tendancielles (2 paragraphes)
## 4. Variabilité interindividuelle comme ressource analytique (2 paragraphes — valoriser la diversité des profils)
## 5. Éléments de réponse à l'Hypothèse 3 (2 paragraphes)
## 6. Limites méthodologiques (1 paragraphe concis)

Données par enseignant : {det}

IMPORTANT : 6-8 pages maximum, prose continue et dense, nuance épistémique systématique.""", 4000)
            if prose2:
                body += md_to_html(prose2)

            html_doc = build_doc_html(body, "Synthèse APPEC")
            progress.progress(1.0, "✅ Terminé !")
            st.download_button("📥 Télécharger la synthèse complète (.doc)",
                              data=html_doc.encode("utf-8"),
                              file_name="Synthese_Complete_APPEC.doc",
                              mime="application/msword")

    st.divider()
    st.subheader("💾 Sauvegarde / Restauration")
    save_data = json.dumps({"teachers": st.session_state.teachers, "prefix_elv": st.session_state.prefix_elv})
    st.download_button("💾 Exporter toutes les données (.json)", data=save_data,
                      file_name="AALC_sauvegarde.json", mime="application/json")
    restore = st.file_uploader("Recharger une sauvegarde (.json)", type=["json"])
    if restore:
        try:
            data_r = json.loads(restore.read())
            if "teachers" in data_r:
                for e in ENSEIGNANTS:
                    if e not in data_r["teachers"]: data_r["teachers"][e] = {"transcript":"","noeuds":[],"prefix_ens":""}
                st.session_state.teachers = data_r["teachers"]
                if "prefix_elv" in data_r: st.session_state.prefix_elv = data_r["prefix_elv"]
                st.success("✅ Données restaurées.")
                st.rerun()
        except Exception as ex:
            st.error(f"❌ {ex}")
