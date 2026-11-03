/**
 * Километраж рейсов по Google Картам для таблицы «Ежедневные направления».
 *
 * Как подключить: в таблице меню «Расширения» → «Apps Script», вставить этот файл
 * целиком вместо содержимого Code.gs, сохранить, обновить таблицу. Появится меню
 * «Километраж». Подробная инструкция в README.md рядом с этим файлом.
 *
 * Что делает: на каждом листе машины (названия листов из цифр: 975, 814, …)
 * берёт адрес загрузки (G) и адреса выгрузки (H), строит маршрут
 * «загрузка → выгрузка 1 → выгрузка 2 → …» через Google Карты и пишет
 * километры в колонку «Км» (N). Уже заполненные км не трогает.
 * В примечании к ячейке «Км» остаются участки маршрута и дата расчёта.
 */

var KM = {
  FIRST_ROW: 29,       // первая строка рейсов
  COL_LOAD: 7,         // G — адрес загрузки
  COL_UNLOAD: 8,       // H — адрес(а) выгрузки
  COL_STATUS: 9,       // I — статус рейса
  COL_COMMENT: 11,     // K — комментарий по рейсу
  COL_KM: 14,          // N — км
  COUNTRY_HINT: 'Россия',
  MAX_PER_RUN: 150     // предохранитель от перерасхода дневной квоты Google
};

// ---------------------------------------------------------------- меню

function onOpen() {
  SpreadsheetApp.getUi().createMenu('Километраж')
    .addItem('Рассчитать пустые км (все машины)', 'fillKm')
    .addItem('Пересчитать км в выделенных строках', 'recalcSelection')
    .addSeparator()
    .addItem('Включить автозапуск (раз в час)', 'enableHourlyTrigger')
    .addItem('Выключить автозапуск', 'disableTriggers')
    .addToUi();
}

// ---------------------------------------------------------------- запуск

/** Заполняет пустые км на всех листах машин. Запускается из меню и по таймеру. */
function fillKm() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var stats = { done: 0, cancelled: 0, failed: 0, skipped: 0, lines: [] };
  var sheets = ss.getSheets().filter(function (sh) { return /^\d+$/.test(sh.getName()); });

  for (var i = 0; i < sheets.length; i++) {
    if (stats.done + stats.failed >= KM.MAX_PER_RUN) break;
    processSheet_(sheets[i], null, false, stats);
  }
  report_(stats);
}

/** Пересчитывает км в выделенных строках активного листа, даже если они уже заполнены. */
function recalcSelection() {
  var sh = SpreadsheetApp.getActiveSheet();
  if (!/^\d+$/.test(sh.getName())) {
    SpreadsheetApp.getUi().alert('Откройте лист машины (975, 814, …) и выделите строки рейсов.');
    return;
  }
  var sel = sh.getActiveRange();
  var rows = [];
  for (var r = sel.getRow(); r < sel.getRow() + sel.getNumRows(); r++) {
    if (r >= KM.FIRST_ROW) rows.push(r);
  }
  var stats = { done: 0, cancelled: 0, failed: 0, skipped: 0, lines: [] };
  processSheet_(sh, rows, true, stats);
  report_(stats);
}

function enableHourlyTrigger() {
  disableTriggers();
  ScriptApp.newTrigger('fillKm').timeBased().everyHours(1).create();
  toast_('Автозапуск включён: пустые км будут заполняться каждый час.');
}

function disableTriggers() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'fillKm') ScriptApp.deleteTrigger(t);
  });
}

// ---------------------------------------------------------------- обработка листа

/**
 * @param {Sheet} sh лист машины
 * @param {number[]|null} onlyRows номера строк (1-based) или null — все строки
 * @param {boolean} overwrite пересчитывать уже заполненные км
 */
function processSheet_(sh, onlyRows, overwrite, stats) {
  var last = sh.getLastRow();
  if (last < KM.FIRST_ROW) return;
  var width = KM.COL_KM - KM.COL_LOAD + 1;
  var values = sh.getRange(KM.FIRST_ROW, KM.COL_LOAD, last - KM.FIRST_ROW + 1, width).getValues();

  for (var i = 0; i < values.length; i++) {
    var row = KM.FIRST_ROW + i;
    if (onlyRows && onlyRows.indexOf(row) === -1) continue;
    if (stats.done + stats.failed >= KM.MAX_PER_RUN) return;

    var load = values[i][0];
    var unload = values[i][KM.COL_UNLOAD - KM.COL_LOAD];
    var status = String(values[i][KM.COL_STATUS - KM.COL_LOAD] || '');
    var comment = String(values[i][KM.COL_COMMENT - KM.COL_LOAD] || '');
    var km = values[i][width - 1];

    if (!load || !unload) continue;                         // пустая строка
    if (!overwrite && km !== '' && km !== null) { stats.skipped++; continue; }

    var cell = sh.getRange(row, KM.COL_KM);
    var tag = sh.getName() + ' стр. ' + row;

    if (/отмен/i.test(status) || /отмен/i.test(comment)) {
      cell.setValue(0);
      cell.setNote('Рейс отменён — 0 км. ' + stamp_());
      stats.cancelled++;
      continue;
    }

    var points = parsePoints_(load).concat(parsePoints_(unload));
    if (points.length < 2) {
      cell.setNote('Не удалось разобрать адреса. ' + stamp_());
      stats.failed++;
      stats.lines.push(tag + ': не разобраны адреса');
      continue;
    }

    try {
      var legs = routeLegs_(points);
      var total = 0;
      var noteLines = [];
      for (var k = 0; k < legs.length; k++) {
        total += legs[k].km;
        noteLines.push((k + 1) + '. ' + legs[k].from + ' → ' + legs[k].to + ': ' + Math.round(legs[k].km) + ' км');
      }
      cell.setValue(Math.round(total));
      cell.setNote('Google Карты, ' + stamp_() + '\n' + noteLines.join('\n') + '\nИтого: ' + Math.round(total) + ' км');
      stats.done++;
    } catch (e) {
      var msg = String(e && e.message ? e.message : e);
      cell.setNote('Маршрут не построен: ' + msg + '\n' + stamp_() + '\nПроверьте адреса или впишите км вручную.');
      stats.failed++;
      stats.lines.push(tag + ': ' + msg);
      if (/quota|лимит|OVER_QUERY_LIMIT/i.test(msg)) {
        stats.lines.push('Достигнута дневная квота Google Карт, остальное посчитается в следующий раз.');
        return;
      }
    }
    Utilities.sleep(200);
  }
}

// ---------------------------------------------------------------- маршрут

/** Строит маршрут по точкам. Возвращает участки [{from, to, km}]. */
function routeLegs_(points) {
  try {
    return directions_(points);
  } catch (e) {
    // Если целиком не строится, пробуем по участкам — так видно, какой адрес виноват.
    if (points.length === 2) throw e;
    var legs = [];
    for (var i = 0; i < points.length - 1; i++) {
      try {
        legs = legs.concat(directions_([points[i], points[i + 1]]));
      } catch (e2) {
        throw new Error('участок «' + points[i] + '» → «' + points[i + 1] + '»: ' + e2.message);
      }
    }
    return legs;
  }
}

function directions_(points) {
  var finder = Maps.newDirectionFinder()
    .setMode(Maps.DirectionFinder.Mode.DRIVING)
    .setLanguage('ru')
    .setRegion('ru')
    .setOrigin(points[0])
    .setDestination(points[points.length - 1]);
  for (var i = 1; i < points.length - 1; i++) finder.addWaypoint(points[i]);

  var res = finder.getDirections();
  if (res.status !== 'OK' || !res.routes || !res.routes.length) {
    throw new Error('Google ответил ' + res.status + (res.error_message ? ' (' + res.error_message + ')' : ''));
  }
  return res.routes[0].legs.map(function (leg) {
    return { from: leg.start_address, to: leg.end_address, km: leg.distance.value / 1000 };
  });
}

// ---------------------------------------------------------------- адреса

/**
 * Разбирает ячейку с адресами на список точек.
 * «1) Краснодар … 2) Новороссийск …» → две точки; без нумерации — одна точка.
 */
function parsePoints_(text) {
  var s = String(text || '').replace(/\r/g, '\n');
  var parts = s.split(/(?:^|\s)\d{1,2}\)\s*/).map(function (p) { return p.trim(); }).filter(Boolean);
  var points = parts.length > 1 ? parts : [s];
  var cleaned = points.map(cleanAddress_).filter(Boolean);
  // убираем подряд идущие дубли (одна точка вписана дважды)
  return cleaned.filter(function (p, i) { return i === 0 || norm_(p) !== norm_(cleaned[i - 1]); });
}

function cleanAddress_(a) {
  var s = String(a).replace(/\s*\n\s*/g, ' ').replace(/\s+/g, ' ').trim();
  s = s.replace(/\s[—–]\s.*$/, '');           // название склада после длинного тире: «— РЦ 5 Самара»
  s = s.replace(/^[.,;\s]+|[.,;\s]+$/g, '');
  if (!s) return '';
  if (!/россия/i.test(s)) s += ', ' + KM.COUNTRY_HINT;
  return s;
}

function norm_(s) { return String(s).toLowerCase().replace(/[^a-zа-я0-9]/g, ''); }

// ---------------------------------------------------------------- служебное

function stamp_() {
  return Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'dd.MM.yyyy HH:mm');
}

function report_(stats) {
  var text = 'Рассчитано: ' + stats.done + ', отменённых (0 км): ' + stats.cancelled +
    ', не построено: ' + stats.failed + ', уже были заполнены: ' + stats.skipped + '.';
  if (stats.lines.length) text += '\n' + stats.lines.join('\n');
  Logger.log(text);
  toast_(text);
}

function toast_(text) {
  try { SpreadsheetApp.getActiveSpreadsheet().toast(text, 'Километраж', 15); } catch (e) { /* по таймеру нет интерфейса */ }
}
