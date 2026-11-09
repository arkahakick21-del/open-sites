# -*- coding: utf-8 -*-
"""Генератор БДДС для ООО «ММА23» (грузоперевозки) на 2027 год."""
import datetime as dt
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter as L
from openpyxl.formatting.rule import FormulaRule
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.chart import LineChart, BarChart, Reference
from openpyxl.comments import Comment

OUT = "MMA23_BDDS_2027.xlsx"

FONT = "Arial"
BLUE = "0000FF"; BLACK = "000000"; GREEN = "008000"
HDR_BG = "1F3864"; SEC_BG = "D9E1F2"; TOTAL_BG = "F2F2F2"; INPUT_BG = "FFF9E5"; KPI_BG = "EEF3FA"
RUB = '#,##0;(#,##0);"-"'
PCT = '0%;(0%);"-"'
MON = '[$-419]MMM YYYY'
DATE = 'DD.MM.YYYY'
thin = Side(style="thin", color="BFBFBF")
BORDER = Border(left=thin, right=thin, top=thin, bottom=thin)

def f(bold=False, color=BLACK, size=10, italic=False):
    return Font(name=FONT, bold=bold, color=color, size=size, italic=italic)

def fill(c): return PatternFill("solid", fgColor=c)

wb = Workbook()
ws_sum = wb.active; ws_sum.title = "Сводка"
ws_plan = wb.create_sheet("План")
ws_fact = wb.create_sheet("Факт")
ws_log = wb.create_sheet("Платежи")
ws_set = wb.create_sheet("Настройки")

# ---------------------------------------------------------------- Настройки
# Параметры
ws_set["A1"] = "Настройки и справочник статей"; ws_set["A1"].font = f(True, size=14)
ws_set["A2"] = "Синие ячейки на жёлтом фоне заполняются вручную. Чёрные — формулы, их не трогаем."
ws_set["A2"].font = f(italic=True, color="595959")
params = [
    ("Компания", "ООО «ММА23»", None, "Название подставляется в заголовки листов."),
    ("Первый месяц бюджета", dt.date(2027, 1, 1), DATE, "Смените дату, и все 12 месяцев пересчитаются."),
    ("Остаток денег на начало, руб.", 1500000, RUB, "Деньги на всех счетах и в кассе на утро первого дня бюджета. Источник: укажите выписки банка."),
    ("Неснижаемый остаток (подушка), руб.", 500000, RUB, "Ниже этой суммы остаток опускаться не должен: топливо и зарплата на две недели."),
]
ws_set["A4"] = "Параметр"; ws_set["B4"] = "Значение"; ws_set["C4"] = "Пояснение"
for c in "ABC":
    ws_set[f"{c}4"].font = f(True, "FFFFFF"); ws_set[f"{c}4"].fill = fill(HDR_BG)
for i, (name, val, fmt, note) in enumerate(params, start=5):
    ws_set[f"A{i}"] = name; ws_set[f"A{i}"].font = f()
    ws_set[f"B{i}"] = val; ws_set[f"B{i}"].font = f(color=BLUE); ws_set[f"B{i}"].fill = fill(INPUT_BG)
    if fmt: ws_set[f"B{i}"].number_format = fmt
    ws_set[f"C{i}"] = note; ws_set[f"C{i}"].font = f(color="595959")
    for c in "ABC": ws_set[f"{c}{i}"].border = BORDER
P_COMPANY, P_START, P_OPEN, P_MIN = "Настройки!$B$5", "Настройки!$B$6", "Настройки!$B$7", "Настройки!$B$8"

# Справочник статей: (статья, направление, раздел)
OP_IN = [
    "Оплата заказчиков за перевозки",
    "Авансы заказчиков",
    "Возвраты, компенсации, прочие поступления",
]
OP_OUT = [
    "Топливо",
    "Зарплата водителей и суточные",
    "Зарплата офиса и диспетчеров",
    "Страховые взносы с зарплаты",
    "Налоги (НДС, прибыль, транспортный)",
    "Погашение долга ФНС по мировому соглашению",
    "Ремонт, ТО, шины, запчасти",
    "Платные дороги, Платон, штрафы",
    "Стоянки, мойки, расходы в рейсе",
    "Страхование (ОСАГО, КАСКО, груз)",
    "Связь, ГЛОНАСС, программы",
    "Аренда офиса и базы",
    "Банк: обслуживание и комиссии",
    "Прочие расходы",
]
INV_IN = ["Продажа техники и имущества"]
INV_OUT = ["Покупка техники, прицепов, оборудования"]
FIN_IN = ["Получение кредитов и займов", "Займы от собственника"]
FIN_OUT = ["Лизинговые платежи", "Погашение кредитов и процентов", "Выплаты собственнику"]

ref_rows = []  # (row_in_settings, name, dir, section)
r0 = 11
ws_set[f"A{r0-1}"] = "Справочник статей (названия можно менять, они подставятся во все листы)"
ws_set[f"A{r0-1}"].font = f(True, size=12)
ws_set[f"A{r0}"] = "Статья"; ws_set[f"B{r0}"] = "Направление"; ws_set[f"C{r0}"] = "Раздел"
for c in "ABC":
    ws_set[f"{c}{r0}"].font = f(True, "FFFFFF"); ws_set[f"{c}{r0}"].fill = fill(HDR_BG)
r = r0 + 1
for names, d, s in [(OP_IN, "Поступление", "Операционная"), (OP_OUT, "Выплата", "Операционная"),
                    (INV_IN, "Поступление", "Инвестиционная"), (INV_OUT, "Выплата", "Инвестиционная"),
                    (FIN_IN, "Поступление", "Финансовая"), (FIN_OUT, "Выплата", "Финансовая")]:
    for n in names:
        ws_set[f"A{r}"] = n; ws_set[f"A{r}"].font = f(color=BLUE); ws_set[f"A{r}"].fill = fill(INPUT_BG)
        ws_set[f"B{r}"] = d; ws_set[f"C{r}"] = s
        for c in "BC": ws_set[f"{c}{r}"].font = f(color=BLUE); ws_set[f"{c}{r}"].fill = fill(INPUT_BG)
        for c in "ABC": ws_set[f"{c}{r}"].border = BORDER
        ref_rows.append((r, n, d, s)); r += 1
REF_FIRST, REF_LAST = r0 + 1, r - 1
ws_set.column_dimensions["A"].width = 46; ws_set.column_dimensions["B"].width = 18; ws_set.column_dimensions["C"].width = 70
ws_set.freeze_panes = "A5"

# ---------------------------------------------------------------- План / Факт (общая структура)
MONTHS = 12
FIRST_COL = 2  # B
LAST_COL = FIRST_COL + MONTHS - 1  # M
TOT_COL = LAST_COL + 1  # N
HDR_ROW = 5
mcols = [L(c) for c in range(FIRST_COL, LAST_COL + 1)]
TOT = L(TOT_COL)

def block_rows():
    """Возвращает план строк: список (kind, payload). kind: section, item(refrow), subtotal, calc."""
    plan = []
    plan.append(("open", None))
    plan.append(("section", "ОПЕРАЦИОННАЯ ДЕЯТЕЛЬНОСТЬ"))
    plan.append(("sub_hdr", "Поступления"))
    op_in = [x for x in ref_rows if x[3] == "Операционная" and x[2] == "Поступление"]
    for x in op_in: plan.append(("item", x))
    plan.append(("subtotal", ("Итого поступления (операционные)", "op_in")))
    plan.append(("sub_hdr", "Выплаты"))
    op_out = [x for x in ref_rows if x[3] == "Операционная" and x[2] == "Выплата"]
    for x in op_out: plan.append(("item", x))
    plan.append(("subtotal", ("Итого выплаты (операционные)", "op_out")))
    plan.append(("flow", ("Операционный денежный поток", "op_in", "op_out")))
    plan.append(("section", "ИНВЕСТИЦИОННАЯ ДЕЯТЕЛЬНОСТЬ"))
    for x in [x for x in ref_rows if x[3] == "Инвестиционная" and x[2] == "Поступление"]: plan.append(("item", x))
    plan.append(("subtotal", ("Итого поступления (инвестиционные)", "inv_in")))
    for x in [x for x in ref_rows if x[3] == "Инвестиционная" and x[2] == "Выплата"]: plan.append(("item", x))
    plan.append(("subtotal", ("Итого выплаты (инвестиционные)", "inv_out")))
    plan.append(("flow", ("Инвестиционный денежный поток", "inv_in", "inv_out")))
    plan.append(("section", "ФИНАНСОВАЯ ДЕЯТЕЛЬНОСТЬ"))
    for x in [x for x in ref_rows if x[3] == "Финансовая" and x[2] == "Поступление"]: plan.append(("item", x))
    plan.append(("subtotal", ("Итого поступления (финансовые)", "fin_in")))
    for x in [x for x in ref_rows if x[3] == "Финансовая" and x[2] == "Выплата"]: plan.append(("item", x))
    plan.append(("subtotal", ("Итого выплаты (финансовые)", "fin_out")))
    plan.append(("flow", ("Финансовый денежный поток", "fin_in", "fin_out")))
    plan.append(("section", "ИТОГИ МЕСЯЦА"))
    plan.append(("net", "Чистый денежный поток за месяц"))
    plan.append(("close", "Остаток денег на конец месяца"))
    plan.append(("minrow", "Неснижаемый остаток (из Настроек)"))
    plan.append(("gap", "Запас денег сверх подушки (минус = кассовый разрыв)"))
    return plan

# Примерный план (руб./мес) — заменить своими цифрами
EXAMPLE_PLAN = {
    "Оплата заказчиков за перевозки": [4600000]*12,
    "Авансы заказчиков": [300000]*12,
    "Возвраты, компенсации, прочие поступления": [50000]*12,
    "Топливо": [1500000]*12,
    "Зарплата водителей и суточные": [900000]*12,
    "Зарплата офиса и диспетчеров": [250000]*12,
    "Страховые взносы с зарплаты": [345000]*12,
    "Налоги (НДС, прибыль, транспортный)": [150000, 150000, 150000, 450000, 150000, 150000, 450000, 150000, 150000, 450000, 150000, 150000],
    "Погашение долга ФНС по мировому соглашению": [200000]*12,
    "Ремонт, ТО, шины, запчасти": [300000]*12,
    "Платные дороги, Платон, штрафы": [180000]*12,
    "Стоянки, мойки, расходы в рейсе": [90000]*12,
    "Страхование (ОСАГО, КАСКО, груз)": [60000, 60000, 400000, 60000, 60000, 60000, 60000, 60000, 60000, 60000, 60000, 60000],
    "Связь, ГЛОНАСС, программы": [25000]*12,
    "Аренда офиса и базы": [120000]*12,
    "Банк: обслуживание и комиссии": [15000]*12,
    "Прочие расходы": [40000]*12,
    "Продажа техники и имущества": [0]*12,
    "Покупка техники, прицепов, оборудования": [0, 0, 0, 0, 0, 1200000, 0, 0, 0, 0, 0, 0],
    "Получение кредитов и займов": [0]*12,
    "Займы от собственника": [0]*12,
    "Лизинговые платежи": [350000]*12,
    "Погашение кредитов и процентов": [0]*12,
    "Выплаты собственнику": [0, 0, 0, 200000, 0, 0, 200000, 0, 0, 200000, 0, 0],
}

def build_sheet(ws, mode):
    """mode: 'plan' (ввод) или 'fact' (SUMIFS из журнала)."""
    title = "План движения денег" if mode == "plan" else "Факт движения денег (считается из листа «Платежи»)"
    ws["A1"] = f'={P_COMPANY}&" — {title}"'; ws["A1"].font = f(True, size=14)
    ws["A2"] = ("Синие ячейки на жёлтом фоне — ваш план на месяц, в рублях, без знака минус. Итоги и остатки считаются сами."
                if mode == "plan" else
                "Здесь ничего не вводится. Каждая сумма — это сумма строк листа «Платежи» по статье и месяцу. Месяцы без записей остаются пустыми.")
    ws["A2"].font = f(italic=True, color="595959")
    ws["A3"] = "Все суммы в рублях."; ws["A3"].font = f(italic=True, color="595959")
    # заголовок месяцев
    ws[f"A{HDR_ROW}"] = "Статья"
    for i, col in enumerate(mcols):
        c = ws[f"{col}{HDR_ROW}"]
        c.value = f"={P_START}" if i == 0 else f"=EDATE({mcols[i-1]}{HDR_ROW},1)"
        c.number_format = MON
    ws[f"{TOT}{HDR_ROW}"] = "Итого за год"
    for col in ["A"] + mcols + [TOT]:
        c = ws[f"{col}{HDR_ROW}"]; c.font = f(True, "FFFFFF"); c.fill = fill(HDR_BG)
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True); c.border = BORDER
    ws.row_dimensions[HDR_ROW].height = 30
    if mode == "fact":
        ws[f"A{HDR_ROW+1}"] = "Есть записи за месяц?"
        ws[f"A{HDR_ROW+1}"].font = f(italic=True, color="595959")
        for col in mcols:
            c = ws[f"{col}{HDR_ROW+1}"]
            c.value = f'=IF(COUNTIFS(Платежи!$G:$G,{col}${HDR_ROW})>0,"да","")'
            c.font = f(italic=True, color="595959"); c.alignment = Alignment(horizontal="center")
        start_row = HDR_ROW + 2
    else:
        start_row = HDR_ROW + 1

    rows = {}      # key -> row number (open, close, net, subtotal keys, flows)
    groups = {}    # key -> (first_row, last_row) of items
    r = start_row
    plan = block_rows()
    pending_group_start = None
    current_group = None
    for kind, payload in plan:
        if kind == "item" and pending_group_start is None:
            pending_group_start = r
        if kind == "open":
            ws[f"A{r}"] = "Остаток денег на начало месяца"; ws[f"A{r}"].font = f(True)
            rows["open"] = r
            r += 1
        elif kind == "section":
            ws[f"A{r}"] = payload
            for col in ["A"] + mcols + [TOT]:
                ws[f"{col}{r}"].fill = fill(SEC_BG); ws[f"{col}{r}"].font = f(True)
            r += 1
        elif kind == "sub_hdr":
            ws[f"A{r}"] = payload; ws[f"A{r}"].font = f(True, italic=True, color="595959")
            r += 1
        elif kind == "item":
            ref_r, name, d, s = payload
            ws[f"A{r}"] = f"=Настройки!$A${ref_r}"; ws[f"A{r}"].font = f(color=GREEN)
            ws[f"A{r}"].alignment = Alignment(indent=1)
            for i, col in enumerate(mcols):
                c = ws[f"{col}{r}"]
                if mode == "plan":
                    c.value = EXAMPLE_PLAN.get(name, [0]*12)[i]
                    c.font = f(color=BLUE); c.fill = fill(INPUT_BG)
                else:
                    c.value = f"=SUMIFS(Платежи!$D:$D,Платежи!$C:$C,$A{r},Платежи!$G:$G,{col}${HDR_ROW})"
                    c.font = f()
                c.number_format = RUB
            ws[f"{TOT}{r}"] = f"=SUM({mcols[0]}{r}:{mcols[-1]}{r})"; ws[f"{TOT}{r}"].number_format = RUB
            ws[f"{TOT}{r}"].font = f(True)
            rows[("item", name)] = r
            r += 1
        elif kind == "subtotal":
            label, key = payload
            first, last = pending_group_start, r - 1
            groups[key] = (first, last); pending_group_start = None
            ws[f"A{r}"] = label; ws[f"A{r}"].font = f(True)
            for col in mcols + [TOT]:
                c = ws[f"{col}{r}"]; c.value = f"=SUM({col}{first}:{col}{last})"
                c.number_format = RUB; c.font = f(True); c.fill = fill(TOTAL_BG)
            ws[f"A{r}"].fill = fill(TOTAL_BG)
            rows[key] = r
            r += 1
        elif kind == "flow":
            label, kin, kout = payload
            ws[f"A{r}"] = label; ws[f"A{r}"].font = f(True)
            for col in mcols + [TOT]:
                c = ws[f"{col}{r}"]; c.value = f"={col}{rows[kin]}-{col}{rows[kout]}"
                c.number_format = RUB; c.font = f(True)
            rows[("flow", kin)] = r
            r += 1
        elif kind == "net":
            ws[f"A{r}"] = payload; ws[f"A{r}"].font = f(True)
            for col in mcols + [TOT]:
                c = ws[f"{col}{r}"]
                c.value = f"={col}{rows[('flow','op_in')]}+{col}{rows[('flow','inv_in')]}+{col}{rows[('flow','fin_in')]}"
                c.number_format = RUB; c.font = f(True)
            rows["net"] = r
            r += 1
        elif kind == "close":
            ws[f"A{r}"] = payload; ws[f"A{r}"].font = f(True, size=11)
            for col in mcols:
                c = ws[f"{col}{r}"]
                if mode == "plan":
                    c.value = f"={col}{rows['open']}+{col}{rows['net']}"
                else:
                    c.value = f'=IF({col}${HDR_ROW+1}="","",{col}{rows["open"]}+{col}{rows["net"]})'
                c.number_format = RUB; c.font = f(True, size=11); c.fill = fill(TOTAL_BG)
            ws[f"A{r}"].fill = fill(TOTAL_BG)
            # итог года для остатка: остаток на конец последнего месяца
            if mode == "plan":
                ws[f"{TOT}{r}"] = f"={mcols[-1]}{r}"
            else:
                ws[f"{TOT}{r}"] = f'=IF(COUNT({mcols[0]}{r}:{mcols[-1]}{r})=0,"",LOOKUP(9.9E+307,{mcols[0]}{r}:{mcols[-1]}{r}))'
            ws[f"{TOT}{r}"].number_format = RUB; ws[f"{TOT}{r}"].font = f(True, size=11); ws[f"{TOT}{r}"].fill = fill(TOTAL_BG)
            rows["close"] = r
            r += 1
        elif kind == "minrow":
            ws[f"A{r}"] = payload; ws[f"A{r}"].font = f(color="595959")
            for col in mcols:
                c = ws[f"{col}{r}"]; c.value = f"={P_MIN}"; c.number_format = RUB; c.font = f(color=GREEN)
            rows["min"] = r
            r += 1
        elif kind == "gap":
            ws[f"A{r}"] = payload; ws[f"A{r}"].font = f(True)
            for col in mcols:
                c = ws[f"{col}{r}"]
                c.value = f'=IF({col}{rows["close"]}="","",{col}{rows["close"]}-{col}{rows["min"]})'
                c.number_format = RUB; c.font = f(True)
            rows["gap"] = r
            r += 1
    # остаток на начало: январь из Настроек, далее — конец предыдущего месяца
    ro = rows["open"]
    for i, col in enumerate(mcols):
        c = ws[f"{col}{ro}"]
        if i == 0:
            c.value = f"={P_OPEN}"; c.font = f(True, color=GREEN)
        else:
            prev = mcols[i-1]
            c.value = (f"={prev}{rows['close']}" if mode == "plan"
                       else f'=IF({col}${HDR_ROW+1}="","",{prev}{rows["close"]})')
            c.font = f(True)
        c.number_format = RUB
    ws[f"{TOT}{ro}"] = f"={mcols[0]}{ro}"; ws[f"{TOT}{ro}"].number_format = RUB; ws[f"{TOT}{ro}"].font = f(True)
    last_row = r - 1
    # границы и ширины
    for rr in range(HDR_ROW, last_row + 1):
        for col in ["A"] + mcols + [TOT]:
            ws[f"{col}{rr}"].border = BORDER
    ws.column_dimensions["A"].width = 50
    for col in mcols: ws.column_dimensions[col].width = 13
    ws.column_dimensions[TOT].width = 15
    ws.freeze_panes = f"B{start_row}"
    # кассовый разрыв — красным
    red = PatternFill("solid", fgColor="F8CBAD")
    rng = f"{mcols[0]}{rows['gap']}:{mcols[-1]}{rows['gap']}"
    ws.conditional_formatting.add(rng, FormulaRule(formula=[f'AND({mcols[0]}{rows["gap"]}<>"",{mcols[0]}{rows["gap"]}<0)'], fill=red, font=Font(name=FONT, bold=True, color="9C0006")))
    rng2 = f"{mcols[0]}{rows['close']}:{mcols[-1]}{rows['close']}"
    ws.conditional_formatting.add(rng2, FormulaRule(formula=[f'AND({mcols[0]}{rows["close"]}<>"",{mcols[0]}{rows["close"]}<0)'], fill=red, font=Font(name=FONT, bold=True, color="9C0006")))
    return rows, groups, last_row

plan_rows, plan_groups, plan_last = build_sheet(ws_plan, "plan")
fact_rows, fact_groups, fact_last = build_sheet(ws_fact, "fact")
# пояснение к примерному плану
ws_plan[f"A{plan_last+2}"] = "Суммы в плане примерные (ориентир для парка из нескольких тягачей). Замените их своими цифрами."
ws_plan[f"A{plan_last+2}"].font = f(italic=True, color="595959")

# ---------------------------------------------------------------- Платежи (журнал)
ws_log["A1"] = f'={P_COMPANY}&" — Журнал поступлений и платежей"'; ws_log["A1"].font = f(True, size=14)
ws_log["A2"] = "Одна строка — одна операция (или итог за день по статье). Заполняются синие колонки: дата, статья из списка, сумма без минуса, кому/от кого, комментарий. Направление и месяц ставятся сами."
ws_log["A2"].font = f(italic=True, color="595959")
hdr = ["Дата", "Поступление или выплата", "Статья", "Сумма, руб.", "Контрагент", "Комментарий", "Месяц (служебное)"]
for i, h in enumerate(hdr, start=1):
    c = ws_log.cell(row=4, column=i, value=h)
    c.font = f(True, "FFFFFF"); c.fill = fill(HDR_BG); c.alignment = Alignment(horizontal="center", wrap_text=True); c.border = BORDER
ws_log.row_dimensions[4].height = 30
LOG_FIRST, LOG_LAST = 5, 504
examples = [
    (dt.date(2027, 1, 12), "Оплата заказчиков за перевозки", 850000, "ООО «Заказчик»", "Оплата рейсов за декабрь"),
    (dt.date(2027, 1, 15), "Топливо", 310000, "Топливная карта", "Заправки за первую половину месяца"),
    (dt.date(2027, 1, 20), "Лизинговые платежи", 350000, "Лизинговая компания", "Платёж по графику"),
]
for rr in range(LOG_FIRST, LOG_LAST + 1):
    for col in (1, 3, 4, 5, 6):
        c = ws_log.cell(row=rr, column=col); c.font = f(color=BLUE); c.fill = fill(INPUT_BG); c.border = BORDER
    ws_log.cell(row=rr, column=1).number_format = DATE
    ws_log.cell(row=rr, column=4).number_format = RUB
    b = ws_log.cell(row=rr, column=2, value=f'=IF(C{rr}="","",INDEX(Настройки!$B${REF_FIRST}:$B${REF_LAST},MATCH(C{rr},Настройки!$A${REF_FIRST}:$A${REF_LAST},0)))')
    b.font = f(); b.border = BORDER
    g = ws_log.cell(row=rr, column=7, value=f'=IF(A{rr}="","",DATE(YEAR(A{rr}),MONTH(A{rr}),1))')
    g.font = f(color="595959"); g.number_format = MON; g.border = BORDER
for i, (d, st, amt, cp, cm) in enumerate(examples):
    rr = LOG_FIRST + i
    ws_log.cell(row=rr, column=1, value=d); ws_log.cell(row=rr, column=3, value=st)
    ws_log.cell(row=rr, column=4, value=amt); ws_log.cell(row=rr, column=5, value=cp); ws_log.cell(row=rr, column=6, value=cm)
ws_log.cell(row=LOG_FIRST, column=6).comment = Comment("Три первые строки — пример заполнения. Удалите их, когда начнёте вести журнал.", "БДДС")
dv = DataValidation(type="list", formula1=f"=Настройки!$A${REF_FIRST}:$A${REF_LAST}", allow_blank=True)
dv.error = "Выберите статью из списка на листе «Настройки»"; dv.errorTitle = "Нет такой статьи"
ws_log.add_data_validation(dv); dv.add(f"C{LOG_FIRST}:C{LOG_LAST}")
dvd = DataValidation(type="date", operator="greaterThan", formula1="36526", allow_blank=True)  # после 01.01.2000
ws_log.add_data_validation(dvd); dvd.add(f"A{LOG_FIRST}:A{LOG_LAST}")
widths = {"A": 12, "B": 22, "C": 44, "D": 15, "E": 26, "F": 36, "G": 16}
for k, v in widths.items(): ws_log.column_dimensions[k].width = v
ws_log.freeze_panes = "A5"
ws_log.auto_filter.ref = f"A4:G{LOG_LAST}"

# ---------------------------------------------------------------- Сводка
ws = ws_sum
ws["A1"] = f'={P_COMPANY}&" — БДДС: сводка план/факт"'; ws["A1"].font = f(True, size=16)
ws["A2"] = "Выберите номер месяца в жёлтой ячейке. Ниже — план, факт и отклонение за этот месяц и накопленно с начала года."
ws["A2"].font = f(italic=True, color="595959")
ws["A4"] = "Месяц для анализа (1–12)"; ws["A4"].font = f(True)
ws["B4"] = 1; ws["B4"].font = f(True, BLUE, 12); ws["B4"].fill = fill(INPUT_BG); ws["B4"].border = BORDER
ws["B4"].alignment = Alignment(horizontal="center")
dvm = DataValidation(type="whole", operator="between", formula1="1", formula2="12"); ws.add_data_validation(dvm); dvm.add("B4")
ws["C4"] = f'=EDATE({P_START},B4-1)'; ws["C4"].number_format = '[$-419]MMMM YYYY'; ws["C4"].font = f(True, size=12)
ws["D4"] = '=IF(COUNTIFS(Платежи!$G:$G,C4)>0,"Факт за месяц внесён","По этому месяцу нет записей в журнале «Платежи»")'
ws["D4"].font = f(italic=True, color="595959")
SEL_DATE = "$C$4"

# KPI
kpi_hdr_row = 6
kpis = [
    ("Остаток на начало года", f"={P_OPEN}", f"={P_OPEN}"),
    ("Остаток на конец выбранного месяца", f"=INDEX(План!${mcols[0]}${plan_rows['close']}:${mcols[-1]}${plan_rows['close']},$B$4)",
                                            f"=INDEX(Факт!${mcols[0]}${fact_rows['close']}:${mcols[-1]}${fact_rows['close']},$B$4)"),
    ("Чистый поток с начала года", f"=SUMIF(План!${mcols[0]}${HDR_ROW}:${mcols[-1]}${HDR_ROW},\"<=\"&{SEL_DATE},План!${mcols[0]}${plan_rows['net']}:${mcols[-1]}${plan_rows['net']})",
                                   f"=SUMIF(Факт!${mcols[0]}${HDR_ROW}:${mcols[-1]}${HDR_ROW},\"<=\"&{SEL_DATE},Факт!${mcols[0]}${fact_rows['net']}:${mcols[-1]}${fact_rows['net']})"),
    ("Минимальный остаток за год", f"=MIN(План!${mcols[0]}${plan_rows['close']}:${mcols[-1]}${plan_rows['close']})",
                                   f'=IF(COUNT(Факт!${mcols[0]}${fact_rows["close"]}:${mcols[-1]}${fact_rows["close"]})=0,"",MIN(Факт!${mcols[0]}${fact_rows["close"]}:${mcols[-1]}${fact_rows["close"]}))'),
    ("Месяцев с кассовым разрывом (ниже подушки)", f"=COUNTIF(План!${mcols[0]}${plan_rows['gap']}:${mcols[-1]}${plan_rows['gap']},\"<0\")",
                                                  f"=COUNTIF(Факт!${mcols[0]}${fact_rows['gap']}:${mcols[-1]}${fact_rows['gap']},\"<0\")"),
]
ws[f"A{kpi_hdr_row}"] = "Ключевые показатели"; ws[f"B{kpi_hdr_row}"] = "План"; ws[f"C{kpi_hdr_row}"] = "Факт"
for c in "ABC":
    ws[f"{c}{kpi_hdr_row}"].font = f(True, "FFFFFF"); ws[f"{c}{kpi_hdr_row}"].fill = fill(HDR_BG); ws[f"{c}{kpi_hdr_row}"].border = BORDER
for i, (name, fp, ff) in enumerate(kpis, start=kpi_hdr_row + 1):
    ws[f"A{i}"] = name; ws[f"A{i}"].font = f(); ws[f"A{i}"].fill = fill(KPI_BG)
    ws[f"B{i}"] = fp; ws[f"C{i}"] = ff
    for c in "BC":
        ws[f"{c}{i}"].font = f(True, GREEN); ws[f"{c}{i}"].number_format = RUB if "Месяцев" not in name else "0"
        ws[f"{c}{i}"].fill = fill(KPI_BG)
    for c in "ABC": ws[f"{c}{i}"].border = BORDER
kpi_last = kpi_hdr_row + len(kpis)
ws[f"A{kpi_last+1}"] = "Остаток по факту показывается только за месяцы, по которым есть записи в журнале."
ws[f"A{kpi_last+1}"].font = f(italic=True, color="595959")

# Таблица по статьям
T = kpi_last + 3
heads = ["Статья", "Направление", "План за месяц", "Факт за месяц", "Отклонение", "Исполнение", "План с начала года", "Факт с начала года", "Отклонение с начала года"]
for i, h in enumerate(heads, start=1):
    c = ws.cell(row=T, column=i, value=h); c.font = f(True, "FFFFFF"); c.fill = fill(HDR_BG)
    c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True); c.border = BORDER
ws.row_dimensions[T].height = 32
ws.cell(row=T-1, column=1, value="По статьям за выбранный месяц и с начала года").font = f(True, size=12)
ws.cell(row=T-1, column=5, value="Отклонение = Факт − План. Для поступлений плюс — хорошо, для выплат плюс — перерасход (подсвечивается).").font = f(italic=True, color="595959")

r = T + 1
summary_rows = []
def sum_line(label, prow, frow, bold=False, direction_text="", section_fill=None):
    global r
    ws.cell(row=r, column=1, value=label).font = f(bold)
    ws.cell(row=r, column=2, value=direction_text).font = f(color=GREEN if direction_text.startswith("=") else "595959")
    ws.cell(row=r, column=3, value=f"=INDEX(План!${mcols[0]}${prow}:${mcols[-1]}${prow},$B$4)")
    ws.cell(row=r, column=4, value=f"=INDEX(Факт!${mcols[0]}${frow}:${mcols[-1]}${frow},$B$4)")
    ws.cell(row=r, column=5, value=f"=D{r}-C{r}")
    ws.cell(row=r, column=6, value=f"=IF(C{r}=0,0,D{r}/C{r})")
    ws.cell(row=r, column=7, value=f"=SUMIF(План!${mcols[0]}${HDR_ROW}:${mcols[-1]}${HDR_ROW},\"<=\"&{SEL_DATE},План!${mcols[0]}${prow}:${mcols[-1]}${prow})")
    ws.cell(row=r, column=8, value=f"=SUMIF(Факт!${mcols[0]}${HDR_ROW}:${mcols[-1]}${HDR_ROW},\"<=\"&{SEL_DATE},Факт!${mcols[0]}${frow}:${mcols[-1]}${frow})")
    ws.cell(row=r, column=9, value=f"=H{r}-G{r}")
    for col in range(1, 10):
        c = ws.cell(row=r, column=col); c.border = BORDER
        if col >= 3:
            c.font = f(bold); c.number_format = PCT if col == 6 else RUB
        if section_fill: c.fill = fill(section_fill)
    summary_rows.append(r); r += 1

def section(label):
    global r
    for col in range(1, 10):
        c = ws.cell(row=r, column=col); c.fill = fill(SEC_BG); c.border = BORDER
    ws.cell(row=r, column=1, value=label).font = f(True)
    r += 1

item_first = r
section("ОПЕРАЦИОННАЯ ДЕЯТЕЛЬНОСТЬ")
for key, lbl in [("op_in", "Итого поступления (операционные)"), ("op_out", "Итого выплаты (операционные)")]:
    first, last = plan_groups[key]
    for pr in range(first, last + 1):
        name = [k[1] for k, rr in plan_rows.items() if isinstance(k, tuple) and k[0] == "item" and rr == pr]
        name_ref = f"=План!$A${pr}"
        fr = fact_rows[("item", name[0])]
        # направление берётся из Настроек
        set_row = [x[0] for x in ref_rows if x[1] == name[0]][0]
        sum_line(name_ref, pr, fr, direction_text=f"=Настройки!$B${set_row}")
        ws.cell(row=r-1, column=1).font = f(color=GREEN); ws.cell(row=r-1, column=1).alignment = Alignment(indent=1)
    sum_line(lbl, plan_rows[key], fact_rows[key], bold=True, section_fill=TOTAL_BG)
sum_line("Операционный денежный поток", plan_rows[("flow", "op_in")], fact_rows[("flow", "op_in")], bold=True)
section("ИНВЕСТИЦИОННАЯ ДЕЯТЕЛЬНОСТЬ")
for key, lbl in [("inv_in", "Итого поступления (инвестиционные)"), ("inv_out", "Итого выплаты (инвестиционные)")]:
    first, last = plan_groups[key]
    for pr in range(first, last + 1):
        name = [k[1] for k, rr in plan_rows.items() if isinstance(k, tuple) and k[0] == "item" and rr == pr]
        fr = fact_rows[("item", name[0])]
        set_row = [x[0] for x in ref_rows if x[1] == name[0]][0]
        sum_line(f"=План!$A${pr}", pr, fr, direction_text=f"=Настройки!$B${set_row}")
        ws.cell(row=r-1, column=1).font = f(color=GREEN); ws.cell(row=r-1, column=1).alignment = Alignment(indent=1)
    sum_line(lbl, plan_rows[key], fact_rows[key], bold=True, section_fill=TOTAL_BG)
sum_line("Инвестиционный денежный поток", plan_rows[("flow", "inv_in")], fact_rows[("flow", "inv_in")], bold=True)
section("ФИНАНСОВАЯ ДЕЯТЕЛЬНОСТЬ")
for key, lbl in [("fin_in", "Итого поступления (финансовые)"), ("fin_out", "Итого выплаты (финансовые)")]:
    first, last = plan_groups[key]
    for pr in range(first, last + 1):
        name = [k[1] for k, rr in plan_rows.items() if isinstance(k, tuple) and k[0] == "item" and rr == pr]
        fr = fact_rows[("item", name[0])]
        set_row = [x[0] for x in ref_rows if x[1] == name[0]][0]
        sum_line(f"=План!$A${pr}", pr, fr, direction_text=f"=Настройки!$B${set_row}")
        ws.cell(row=r-1, column=1).font = f(color=GREEN); ws.cell(row=r-1, column=1).alignment = Alignment(indent=1)
    sum_line(lbl, plan_rows[key], fact_rows[key], bold=True, section_fill=TOTAL_BG)
sum_line("Финансовый денежный поток", plan_rows[("flow", "fin_in")], fact_rows[("flow", "fin_in")], bold=True)
section("ИТОГИ")
sum_line("Чистый денежный поток", plan_rows["net"], fact_rows["net"], bold=True, section_fill=TOTAL_BG)
item_last = r - 1
# остаток на конец месяца — отдельной строкой без накопления
ws.cell(row=r, column=1, value="Остаток денег на конец месяца").font = f(True, size=11)
ws.cell(row=r, column=3, value=f"=INDEX(План!${mcols[0]}${plan_rows['close']}:${mcols[-1]}${plan_rows['close']},$B$4)")
ws.cell(row=r, column=4, value=f"=INDEX(Факт!${mcols[0]}${fact_rows['close']}:${mcols[-1]}${fact_rows['close']},$B$4)")
ws.cell(row=r, column=5, value=f'=IF(D{r}="","",D{r}-C{r})')
for col in range(1, 10):
    c = ws.cell(row=r, column=col); c.border = BORDER; c.fill = fill(TOTAL_BG)
    if col >= 3: c.font = f(True, size=11); c.number_format = RUB
close_sum_row = r; r += 1

# подсветка отклонений: перерасход по выплатам и недобор по поступлениям
red = PatternFill("solid", fgColor="F8CBAD"); green_f = PatternFill("solid", fgColor="E2EFDA")
rng = f"E{item_first}:E{item_last}"
ws.conditional_formatting.add(rng, FormulaRule(formula=[f'AND($B{item_first}="Выплата",E{item_first}>0)'], fill=red))
ws.conditional_formatting.add(rng, FormulaRule(formula=[f'AND($B{item_first}="Поступление",E{item_first}<0,$D{item_first}<>0)'], fill=red))
rng = f"I{item_first}:I{item_last}"
ws.conditional_formatting.add(rng, FormulaRule(formula=[f'AND($B{item_first}="Выплата",I{item_first}>0)'], fill=red))
ws.conditional_formatting.add(rng, FormulaRule(formula=[f'AND($B{item_first}="Поступление",I{item_first}<0,$H{item_first}<>0)'], fill=red))
ws.conditional_formatting.add(f"C{close_sum_row}:D{close_sum_row}", FormulaRule(formula=[f'AND(C{close_sum_row}<>"",C{close_sum_row}<{P_MIN})'], fill=red, font=Font(name=FONT, bold=True, color="9C0006")))

wsum_widths = {"A": 46, "B": 14, "C": 15, "D": 15, "E": 14, "F": 11, "G": 16, "H": 16, "I": 18}
for k, v in wsum_widths.items(): ws.column_dimensions[k].width = v
ws.freeze_panes = "A5"

# Данные для графиков (ссылки на План/Факт) + графики
CH = r + 2
ws.cell(row=CH, column=1, value="Данные для графиков (ссылки на листы «План» и «Факт»)").font = f(True, size=12)
ws.cell(row=CH+1, column=1, value="Месяц").font = f(True)
labels = ["Остаток на конец, план", "Остаток на конец, факт", "Поступления, план", "Выплаты, план", "Поступления, факт", "Выплаты, факт"]
for i, lbl in enumerate(labels): ws.cell(row=CH+1, column=2+i, value=lbl).font = f(True)
def total_in(rows_): return [rows_["op_in"], rows_["inv_in"], rows_["fin_in"]]
def total_out(rows_): return [rows_["op_out"], rows_["inv_out"], rows_["fin_out"]]
for i, col in enumerate(mcols):
    rr = CH + 2 + i
    ws.cell(row=rr, column=1, value=f"=TEXT(План!{col}${HDR_ROW},\"MMM\")").font = f(color=GREEN)
    ws.cell(row=rr, column=2, value=f"=План!{col}${plan_rows['close']}")
    ws.cell(row=rr, column=3, value=f'=Факт!{col}${fact_rows["close"]}')
    ws.cell(row=rr, column=4, value="=" + "+".join(f"План!{col}${x}" for x in total_in(plan_rows)))
    ws.cell(row=rr, column=5, value="=" + "+".join(f"План!{col}${x}" for x in total_out(plan_rows)))
    ws.cell(row=rr, column=6, value="=" + "+".join(f"Факт!{col}${x}" for x in total_in(fact_rows)))
    ws.cell(row=rr, column=7, value="=" + "+".join(f"Факт!{col}${x}" for x in total_out(fact_rows)))
    for col_i in range(2, 8):
        ws.cell(row=rr, column=col_i).number_format = RUB; ws.cell(row=rr, column=col_i).font = f(color=GREEN)
ws.cell(row=CH+2+MONTHS, column=1, value="Факт остатка пустой за месяцы без записей в журнале, поэтому красная линия на графике заканчивается на последнем заполненном месяце.").font = f(italic=True, color="595959")

lc = LineChart(); lc.title = "Остаток денег на конец месяца"; lc.height = 8; lc.width = 18
lc.y_axis.title = "руб."; lc.y_axis.numFmt = "#,##0"
data = Reference(ws, min_col=2, max_col=3, min_row=CH+1, max_row=CH+1+MONTHS)
lc.add_data(data, titles_from_data=True)
lc.set_categories(Reference(ws, min_col=1, min_row=CH+2, max_row=CH+1+MONTHS))
lc.series[0].graphicalProperties.line.solidFill = "1F3864"; lc.series[0].graphicalProperties.line.width = 28000
lc.series[1].graphicalProperties.line.solidFill = "C00000"; lc.series[1].graphicalProperties.line.width = 28000
ws.add_chart(lc, "K4")

bc = BarChart(); bc.type = "col"; bc.grouping = "clustered"; bc.title = "Поступления и выплаты по месяцам (план)"; bc.height = 8; bc.width = 18
bc.y_axis.numFmt = "#,##0"
data = Reference(ws, min_col=4, max_col=5, min_row=CH+1, max_row=CH+1+MONTHS)
bc.add_data(data, titles_from_data=True)
bc.set_categories(Reference(ws, min_col=1, min_row=CH+2, max_row=CH+1+MONTHS))
bc.series[0].graphicalProperties.solidFill = "2E75B6"; bc.series[1].graphicalProperties.solidFill = "F4B183"
ws.add_chart(bc, "K22")

# Легенда (как пользоваться)
ws_set["E4"] = "Как работать с БДДС"; ws_set["E4"].font = f(True, size=12)
steps = [
    "1. В Настройках проверьте первый месяц, остаток на начало и подушку. Поправьте названия статей под себя.",
    "2. На листе «План» впишите план по каждой статье на 12 месяцев (синие ячейки). Пример замените своими цифрами.",
    "3. Каждый день или раз в неделю заносите операции в «Платежи»: дата, статья из списка, сумма. Лист «Факт» заполнится сам.",
    "4. На «Сводке» выбирайте месяц и смотрите план, факт и отклонения. Красная заливка — перерасход, недобор или кассовый разрыв.",
    "Цвета: синий на жёлтом — вводите вы; чёрный — формулы; зелёный — ссылки на другие листы.",
]
for i, s in enumerate(steps, start=5):
    ws_set[f"E{i}"] = s; ws_set[f"E{i}"].font = f()
ws_set.column_dimensions["E"].width = 110

# Порядок листов и цвета вкладок
ws_sum.sheet_properties.tabColor = "1F3864"; ws_plan.sheet_properties.tabColor = "2E75B6"
ws_fact.sheet_properties.tabColor = "C00000"; ws_log.sheet_properties.tabColor = "F4B183"; ws_set.sheet_properties.tabColor = "7F7F7F"
for w in wb.worksheets: w.sheet_view.showGridLines = False
ws_log.sheet_view.showGridLines = True
wb.save(OUT)
print("saved", OUT, "plan rows:", plan_rows["open"], plan_rows["close"], "fact close", fact_rows["close"], "ref", REF_FIRST, REF_LAST)
