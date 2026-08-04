"""Testy eksportu tagów: schemat nazwy pliku + wygląd arkusza zgodny z CM360.

Wygląd nie jest ozdobą — arkusz idzie do wydawców i traffickerów, którzy znają plik
z CM360, więc rozjechany styl jest widoczny od razu. Wartości sprawdzane niżej zostały
odczytane z PRAWDZIWEGO eksportu CM360, nie zgadnięte, i ten test pilnuje, żeby zmiana
w `write_xls` ich nie zepsuła. API nie jest tu w ogóle potrzebne.
"""
import datetime
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import export_tags as E

passed = failed = 0


def check(name, got, want):
    global passed, failed
    ok = got == want
    passed += ok; failed += not ok
    print(f"  [{'OK ' if ok else 'FAIL'}] {name}")
    if not ok:
        print(f"        got={got!r}\n        want={want!r}")


CAMP = {"id": "35645113", "name": "Junior_2026"}
ADV = {"id": "9080582", "name": "CG Indywidualny - Konta"}
DAY = datetime.date(2026, 8, 4)

print("nazwa pliku — schemat Tags_kampania_advertiser_data:")
check("kampania, advertiser i data w tej kolejności",
      E.tags_filename(CAMP, ADV, DAY),
      "Tags_Junior_2026_CG Indywidualny - Konta_2026-08-04.xls")
check("data w ISO, żeby pliki jednej kampanii sortowały się chronologicznie",
      E.tags_filename(CAMP, ADV, datetime.date(2026, 12, 31)).endswith("_2026-12-31.xls"), True)
check("znaki zabronione w nazwach Windows nie trafiają do nazwy pliku",
      E.tags_filename({"name": 'A/B:C*D?E"F<G>H|I'}, {"name": "X\\Y"}, DAY),
      "Tags_A_B_C_D_E_F_G_H_I_X_Y_2026-08-04.xls")
check("puste nazwy nie dają pliku o samych podkreśleniach",
      E.tags_filename({"name": ""}, {"name": None}, DAY),
      "Tags_brak_brak_2026-08-04.xls")

# --- wygląd arkusza --------------------------------------------------------
try:
    import xlrd
except ImportError:                                   # pragma: no cover
    print("\n(xlrd niedostępny — pomijam sprawdzenie wyglądu arkusza)")
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)

ROWS = [["9080582", "CG Indywidualny - Konta", "35645113", "Junior_2026", "445804588",
         "", "CG_GDN", "Display", "Display", "300x250", "2026-01-01", "2026-12-31",
         "1", "300x250", "2", "linia1", "<img>", "<iframe>", "<script>", "", "<a>"]]

path = os.path.join(tempfile.gettempdir(), "cmworker_tags_style_test.xls")
E.write_xls(CAMP, ADV, ROWS, path)
wb = xlrd.open_workbook(path, formatting_info=True)
sh = wb.sheet_by_index(0)


def style(r, c):
    xf = wb.xf_list[sh.cell_xf_index(r, c)]
    f = wb.font_list[xf.font_index]
    return {"bold": bool(f.bold), "font": f.name, "pt": f.height // 20,
            "fill": xf.background.fill_pattern,
            "fill_colour": xf.background.pattern_colour_index,
            "border": xf.border.left_line_style,
            "wrap": bool(xf.alignment.text_wrapped)}


print("\nukład arkusza (odwzorowany z eksportu CM360):")
check("nazwa arkusza", sh.name, "Tracking Ads")
check("blok nagłówkowy w wierszach 1..5",
      [sh.cell_value(r, 1) for r in range(1, 6)],
      ["CONTRACT INFORMATION", "Advertiser ID", "Advertiser Name",
       "Campaign ID", "Campaign Name"])
check("sekcja z instrukcjami dla wydawcy (CM ją ma, my wcześniej nie)",
      sh.cell_value(7, 1), E.NOTE_TITLE)
check("treść instrukcji niepusta i wspomina cache-busting",
      "[timestamp]" in sh.cell_value(8, 1), True)
check("nagłówki tabeli w wierszu 10", sh.cell_value(10, 1), "Advertiser ID")
check("21 kolumn nagłówka", [sh.cell_value(10, c) for c in range(1, 22)], E.HEADERS)
check("dane od wiersza 11", sh.cell_value(11, 16), "linia1")

print("\nformatowanie:")
check("cały arkusz Arial 8pt (było 10pt)",
      {style(r, 1)["font"] for r in (1, 2, 7, 10, 11)} |
      {style(r, 1)["pt"] for r in (1, 2, 7, 10, 11)}, {"Arial", 8})
check("nagłówek sekcji: pogrubiony na tle #99CCFF (paleta 44)",
      (style(1, 1)["bold"], style(1, 1)["fill"], style(1, 1)["fill_colour"]),
      (True, 1, 44))
check("nagłówek tabeli: pogrubiony na tym samym tle",
      (style(10, 1)["bold"], style(10, 1)["fill_colour"]), (True, 44))
check("etykiety bloku nagłówkowego pogrubione, wartości nie",
      (style(2, 1)["bold"], style(2, 8)["bold"]), (True, False))
check("ramki na komórkach nagłówka i danych",
      all(style(r, 1)["border"] == 1 for r in (1, 2, 10, 11)), True)
check("zawijanie w komórkach danych (tagi są długie)", style(11, 17)["wrap"], True)
check("kolumny metadanych mają białe wypełnienie, kolumny tagów nie",
      (style(11, 1)["fill_colour"], style(11, 17)["fill"]), (9, 0))

print("\nwymiary i nawigacja:")
check("szerokości kolumn dokładnie jak w CM (jednostki BIFF, nie znaki×256)",
      [sh.computed_column_width(c) for c in range(22)], E.COL_WIDTHS)
check("wysokości: sekcje 315, notatka 2299, dane 867",
      (sh.rowinfo_map[1].height, sh.rowinfo_map[8].height, sh.rowinfo_map[11].height),
      (E.ROW_H_SECTION, E.ROW_H_NOTE, E.ROW_H_DATA))
check("domyślna wysokość wiersza jak w CM", sh.default_row_height, E.DEFAULT_ROW_HEIGHT)
check("scalenia bloku nagłówkowego i notatki",
      sorted(sh.merged_cells),
      sorted([(1, 2, 1, 13), (2, 3, 1, 8), (2, 3, 8, 13), (3, 4, 1, 8), (3, 4, 8, 13),
              (4, 5, 1, 8), (4, 5, 8, 13), (5, 6, 1, 8), (5, 6, 8, 13),
              (7, 8, 1, 13), (8, 9, 1, 13)]))
check("nagłówek zamrożony — przy 100+ tagach widać, która kolumna to który tag",
      (sh.panes_are_frozen, sh.horz_split_pos), (1, E.FIRST_DATA_ROW))

os.remove(path)
print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
