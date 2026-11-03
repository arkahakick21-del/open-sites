/**
 * Километраж рейсов по Google Картам для таблицы «Ежедневные направления».
 *
 * Как подключить: в таблице меню «Расширения» → «Apps Script», вставить этот файл
 * целиком вместо содержимого Code.gs, сохранить. Запуск: выбрать функцию fillKm
 * и нажать «Выполнить», либо через меню «Километраж» в таблице.
 *
 * Что делает: на каждом листе машины (названия листов из цифр: 975, 814, …)
 * берёт адрес загрузки (G) и адреса выгрузки (H), находит каждую точку на карте,
 * строит маршрут «загрузка → выгрузка 1 → выгрузка 2 → …» и пишет километры
 * в колонку «Км» (N). Уже заполненные км не трогает.
 *
 * Лист «Адреса» — справочник: «если в адресе есть …» → «искать на карте как …»
 * (адрес или координаты «широта,долгота»). Нужен для складов, которые Google
 * по сырому адресу находит неверно. Справочник можно пополнять руками.
 *
 * Если адрес не найден или Google нашёл не тот населённый пункт, что написан
 * в адресе, ячейка адреса (G или H) становится КРАСНОЙ, а в примечании к «Км»
 * написано, что именно нашлось. Диспетчер уточняет город прямо в красной ячейке
 * и запускает «Пересчитать красные» — красный снимается, когда маршрут построен.
 */

var KM = {
  FIRST_ROW: 29,       // первая строка рейсов
  COL_LOAD: 7,         // G — адрес загрузки
  COL_UNLOAD: 8,       // H — адрес(а) выгрузки
  COL_STATUS: 9,       // I — статус рейса
  COL_COMMENT: 11,     // K — комментарий по рейсу
  COL_KM: 14,          // N — км
  ALIAS_SHEET: 'Адреса',
  COUNTRY_HINT: 'Россия',
  RED: '#f59999',      // адрес не найден или найден не там: впишите точное название города
  MAX_PER_RUN: 150     // предохранитель от перерасхода дневной квоты Google
};

// ---------------------------------------------------------------- меню

function onOpen() {
  SpreadsheetApp.getUi().createMenu('Километраж')
    .addItem('Рассчитать пустые км (все машины)', 'fillKm')
    .addItem('Пересчитать км в выделенных строках', 'recalcSelection')
    .addItem('Пересчитать красные (уточнённые адреса)', 'recalcMarked')
    .addSeparator()
    .addItem('Включить автозапуск (раз в час)', 'enableHourlyTrigger')
    .addItem('Выключить автозапуск', 'disableTriggers')
    .addToUi();
}

// ---------------------------------------------------------------- запуск

/** Заполняет пустые км на всех листах машин. Запускается из меню и по таймеру. */
function fillKm() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var ctx = { aliases: loadAliases_(ss), done: 0, cancelled: 0, failed: 0, flagged: 0, skipped: 0, lines: [] };
  var sheets = ss.getSheets().filter(function (sh) { return /^\d+$/.test(sh.getName()); });

  for (var i = 0; i < sheets.length; i++) {
    if (ctx.done + ctx.failed >= KM.MAX_PER_RUN) break;
    processSheet_(sheets[i], null, false, ctx);
  }
  report_(ctx);
}

/** Пересчитывает км в выделенных строках активного листа, даже если они уже заполнены. */
function recalcSelection() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sh = ss.getActiveSheet();
  if (!/^\d+$/.test(sh.getName())) {
    SpreadsheetApp.getUi().alert('Откройте лист машины (975, 814, …) и выделите строки рейсов.');
    return;
  }
  var sel = sh.getActiveRange();
  var rows = [];
  for (var r = sel.getRow(); r < sel.getRow() + sel.getNumRows(); r++) {
    if (r >= KM.FIRST_ROW) rows.push(r);
  }
  var ctx = { aliases: loadAliases_(ss), done: 0, cancelled: 0, failed: 0, flagged: 0, skipped: 0, lines: [] };
  processSheet_(sh, rows, true, ctx);
  report_(ctx);
}

/** Пересчитывает рейсы, у которых адрес загрузки или выгрузки помечен красным. */
function recalcMarked() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var ctx = { aliases: loadAliases_(ss), done: 0, cancelled: 0, failed: 0, flagged: 0, skipped: 0, lines: [] };
  var sheets = ss.getSheets().filter(function (sh) { return /^\d+$/.test(sh.getName()); });
  for (var i = 0; i < sheets.length; i++) {
    var sh = sheets[i], last = sh.getLastRow();
    if (last < KM.FIRST_ROW) continue;
    var bg = sh.getRange(KM.FIRST_ROW, KM.COL_LOAD, last - KM.FIRST_ROW + 1, 2).getBackgrounds();
    var rows = [];
    for (var r = 0; r < bg.length; r++) {
      if (isRed_(bg[r][0]) || isRed_(bg[r][1])) rows.push(KM.FIRST_ROW + r);
    }
    if (rows.length) processSheet_(sh, rows, true, ctx);
  }
  report_(ctx);
}

function isRed_(color) { return String(color || '').toLowerCase() === KM.RED.toLowerCase(); }

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
function processSheet_(sh, onlyRows, overwrite, ctx) {
  var last = sh.getLastRow();
  if (last < KM.FIRST_ROW) return;
  var width = KM.COL_KM - KM.COL_LOAD + 1;
  var values = sh.getRange(KM.FIRST_ROW, KM.COL_LOAD, last - KM.FIRST_ROW + 1, width).getValues();

  for (var i = 0; i < values.length; i++) {
    var row = KM.FIRST_ROW + i;
    if (onlyRows && onlyRows.indexOf(row) === -1) continue;
    if (ctx.done + ctx.failed >= KM.MAX_PER_RUN) return;

    var load = values[i][0];
    var unload = values[i][KM.COL_UNLOAD - KM.COL_LOAD];
    var status = String(values[i][KM.COL_STATUS - KM.COL_LOAD] || '');
    var comment = String(values[i][KM.COL_COMMENT - KM.COL_LOAD] || '');
    var km = values[i][width - 1];

    if (!load || !unload) continue;                         // пустая строка
    if (!overwrite && km !== '' && km !== null) { ctx.skipped++; continue; }

    var cell = sh.getRange(row, KM.COL_KM);
    var tag = sh.getName() + ' стр. ' + row;

    if (/отмен/i.test(status) || /отмен/i.test(comment)) {
      cell.setValue(0).setNote('Рейс отменён — 0 км. ' + stamp_()).setBackground(null);
      sh.getRange(row, KM.COL_LOAD, 1, 2).setBackground(null);
      ctx.cancelled++;
      continue;
    }

    var raw = parsePoints_(load).concat(parsePoints_(unload));
    if (raw.length < 2) {
      cell.setNote('Не удалось разобрать адреса. ' + stamp_());
      ctx.failed++;
      ctx.lines.push(tag + ': не разобраны адреса');
      continue;
    }

    var loadCell = sh.getRange(row, KM.COL_LOAD);
    var unloadCell = sh.getRange(row, KM.COL_UNLOAD);
    var nLoad = parsePoints_(load).length;            // сколько точек пришло из колонки загрузки

    try {
      var points = [];
      for (var p = 0; p < raw.length; p++) {
        try {
          points.push(resolvePoint_(raw[p], ctx.aliases));
        } catch (ePoint) {
          (p < nLoad ? loadCell : unloadCell).setBackground(KM.RED);
          throw ePoint;
        }
      }
      var legs;
      try {
        legs = routeLegs_(points);
      } catch (eRoute) {
        loadCell.setBackground(KM.RED);
        unloadCell.setBackground(KM.RED);
        throw eRoute;
      }

      var total = 0, noteLines = [], warns = [], redLoad = false, redUnload = false;
      for (var k = 0; k < legs.length; k++) {
        total += legs[k].km;
        noteLines.push((k + 1) + '. ' + legs[k].from + ' → ' + legs[k].to + ': ' + Math.round(legs[k].km) + ' км');
      }
      points.forEach(function (pt, idx) {
        if (!pt.warn) return;
        warns.push(pt.warn);
        if (idx < nLoad) redLoad = true; else redUnload = true;
      });

      var note = 'Google Карты, ' + stamp_() + '\n' + noteLines.join('\n') + '\nИтого: ' + Math.round(total) + ' км';
      if (warns.length) {
        note = 'ПРОВЕРЬТЕ АДРЕС (красная ячейка). ' + warns.join(' ') + '\nВпишите точное название города в красную ячейку и запустите «Пересчитать красные».\n\n' + note;
        loadCell.setBackground(redLoad ? KM.RED : null);
        unloadCell.setBackground(redUnload ? KM.RED : null);
        ctx.flagged++;
        ctx.lines.push(tag + ': проверьте адрес — ' + warns.join(' '));
      } else {
        loadCell.setBackground(null);
        unloadCell.setBackground(null);
      }
      cell.setValue(Math.round(total)).setNote(note).setBackground(null);
      ctx.done++;
    } catch (e) {
      var msg = String(e && e.message ? e.message : e);
      cell.setNote('Маршрут не построен: ' + msg + '\n' + stamp_() + '\nВпишите точное название города в красную ячейку адреса и запустите «Пересчитать красные», либо добавьте склад на лист «Адреса».');
      ctx.failed++;
      ctx.lines.push(tag + ': ' + msg);
      if (/quota|лимит|OVER_QUERY_LIMIT/i.test(msg)) {
        ctx.lines.push('Достигнута дневная квота Google Карт, остальное посчитается в следующий раз.');
        return;
      }
    }
    Utilities.sleep(200);
  }
}

// ---------------------------------------------------------------- точки и маршрут

/** Справочник с листа «Адреса»: A — фрагмент адреса, B — адрес или координаты для карт, C — название. */
function loadAliases_(ss) {
  var sh = ss.getSheetByName(KM.ALIAS_SHEET);
  if (!sh || sh.getLastRow() < 2) return [];
  var vals = sh.getRange(2, 1, sh.getLastRow() - 1, 3).getValues();
  var out = [];
  vals.forEach(function (r) {
    if (r[0] && r[1]) out.push({ key: norm_(r[0]), value: String(r[1]).trim(), name: String(r[2] || r[0]).trim() });
  });
  return out;
}

/** Находит точку: сначала справочник, потом геокодер Google. Возвращает {latlng, label, warn}. */
function resolvePoint_(addr, aliases) {
  var n = norm_(addr);
  for (var i = 0; i < aliases.length; i++) {
    if (n.indexOf(aliases[i].key) !== -1) {
      var v = aliases[i].value;
      if (isLatLng_(v)) return { latlng: v, label: aliases[i].name + ' (справочник)', warn: '' };
      var ga = geocode_(v);
      if (!ga) throw new Error('адрес из справочника не найден: «' + v + '»');
      return { latlng: ga.lat + ',' + ga.lng, label: ga.formatted, warn: '' };
    }
  }
  var g = geocode_(addr);
  if (!g) {
    var simpler = simplify_(addr);
    if (simpler !== addr) g = geocode_(simpler);
  }
  if (!g) throw new Error('адрес не найден: «' + addr + '»');
  return { latlng: g.lat + ',' + g.lng, label: g.formatted, warn: checkMatch_(addr, g.formatted) };
}

function isLatLng_(s) { return /^-?\d{1,2}(\.\d+)?\s*,\s*-?\d{1,3}(\.\d+)?$/.test(String(s).trim()); }

function geocode_(q) {
  var res = Maps.newGeocoder().setRegion('ru').setLanguage('ru').geocode(q);
  if (res.status === 'OVER_QUERY_LIMIT') throw new Error('OVER_QUERY_LIMIT: превышена квота геокодера');
  if (res.status !== 'OK' || !res.results || !res.results.length) return null;
  var r = res.results[0];
  return { lat: r.geometry.location.lat, lng: r.geometry.location.lng, formatted: r.formatted_address };
}

/** Упрощённый адрес для повторной попытки: без скобок, служебных слов и помещений. */
function simplify_(a) {
  var s = String(a);
  s = s.replace(/\([^)]*\)/g, ' ');
  s = s.replace(/(^|[\s,])(с\/с|с\/п|сп\.|с\.п\.|нежилое|помещени\S*|здание складского( комплекса)?|номер по плану\s*\S+|этаж\s*\S+|литера?\s+\S+|пом\.?\s*\S+)(?=[\s,]|$)/gi, '$1');
  s = s.replace(/(^|[\s,])сп\.(?=\S)/gi, '$1');
  s = s.replace(/\s*,(\s*,)+/g, ', ').replace(/\s+/g, ' ').replace(/\s,/g, ',').replace(/^[\s,.;]+|[\s,.;]+$/g, '');
  return s;
}

/**
 * Проверяет, что в найденном адресе есть населённый пункт (или регион) из исходного.
 * Названия улиц и служебные слова не учитываются. Возвращает '' если всё сходится,
 * иначе текст предупреждения.
 */
function checkMatch_(input, formatted) {
  var stop = /^(обл|область|край|респ|республика|р|рн|район|округ|городской|сельское|поселение|тер|территория|промзона|промышленная|промышленный|индустриальный|парк|логопарк|технопарк|логистический|здание|строение|корпус|литер|литера|этаж|нежилое|помещение|склад|складской|складского|комплекс|комплекса|улица|ул|шоссе|проезд|проспект|переулок|площадка|автодорога|дорога|россия|зона|квартал|массив|жилой|вблизи|границах|земель|ворота|завод|производство|распределительный|центр|номер|плану|город|деревня|посёлок|поселок|село|станица|хутор|дер|пос|рп|пгт|нпс|мо|го|сп|дом|стр|зд|км|мкр|тракт|кольцо|малое|большое|северная|южная|западная|восточная|первая|вторая)$/i;
  var street = /^(ул|улица|ш|шоссе|пр|проспект|пер|переулок|проезд|пр-д|б|бульвар|наб|набережная|тракт|дорога|дор|пл|площадь|тупик|линия|аллея|кольцо)$/i;
  var found = norm_(formatted);
  var candidates = [];
  // смотрим по частям между запятыми, чтобы «Чернореченская, ул. Партизанская» не путалось с «Шоссейная ул»
  String(input).split(/[,;]+/).forEach(function (seg) {
    var words = seg.split(/[^A-Za-zА-Яа-яЁё]+/).filter(Boolean);
    for (var i = 0; i < words.length; i++) {
      var w = words[i];
      if (w.length < 4 || stop.test(w)) continue;
      var prev = words[i - 1] || '', next = words[i + 1] || '';
      if (street.test(prev) || street.test(next)) continue;    // «ул. Партизанская», «Шоссейная ул»
      candidates.push(w);                                       // «Самарская обл» тоже годится как совпадение
    }
  });
  if (!candidates.length) return '';
  for (var k = 0; k < candidates.length; k++) {
    if (found.indexOf(norm_(candidates[k]).slice(0, 5)) !== -1) return '';
  }
  return 'В адресе «' + candidates[0] + '», Google нашёл «' + formatted + '».';
}

/** Строит маршрут по точкам [{latlng,label}]. Возвращает участки [{from, to, km}]. */
function routeLegs_(points) {
  try {
    return directions_(points);
  } catch (e) {
    if (points.length === 2) throw e;
    var legs = [];
    for (var i = 0; i < points.length - 1; i++) {
      try {
        legs = legs.concat(directions_([points[i], points[i + 1]]));
      } catch (e2) {
        throw new Error('участок «' + points[i].label + '» → «' + points[i + 1].label + '»: ' + e2.message);
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
    .setOrigin(points[0].latlng)
    .setDestination(points[points.length - 1].latlng);
  for (var i = 1; i < points.length - 1; i++) finder.addWaypoint(points[i].latlng);

  var res = finder.getDirections();
  if (res.status !== 'OK' || !res.routes || !res.routes.length) {
    throw new Error('Google ответил ' + res.status + (res.error_message ? ' (' + res.error_message + ')' : ''));
  }
  return res.routes[0].legs.map(function (leg, idx) {
    return { from: points[idx].label, to: points[idx + 1].label, km: leg.distance.value / 1000 };
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

function norm_(s) { return String(s).toLowerCase().replace(/ё/g, 'е').replace(/[^a-zа-я0-9]/g, ''); }

// ---------------------------------------------------------------- служебное

function stamp_() {
  return Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'dd.MM.yyyy HH:mm');
}

function report_(ctx) {
  var text = 'Рассчитано: ' + ctx.done + ' (из них проверить адрес: ' + ctx.flagged + '), отменённых (0 км): ' + ctx.cancelled +
    ', не построено: ' + ctx.failed + ', уже были заполнены: ' + ctx.skipped + '.';
  if (ctx.lines.length) text += '\n' + ctx.lines.join('\n');
  Logger.log(text);
  toast_(text);
}

function toast_(text) {
  try { SpreadsheetApp.getActiveSpreadsheet().toast(text, 'Километраж', 15); } catch (e) { /* по таймеру нет интерфейса */ }
}
