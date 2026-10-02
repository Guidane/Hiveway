// Osmia: small helpers for the pages. No build step, no libraries.
(function () {
  'use strict';

  function csrf() {
    var m = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : '';
  }
  function el(tag, attrs, text) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) { node.setAttribute(k, attrs[k]); });
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function postJSON(url, body) {
    return fetch(url, {
      method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrf()},
      body: JSON.stringify(body)
    }).then(function (r) {
      return r.json().catch(function () { return {error: 'The server answered ' + r.status + '.'}; })
        .then(function (data) { if (!r.ok) throw new Error(data.error || 'Could not save.'); return data; });
    });
  }

  // -- Grid: double-click a cell to edit it --------------------------------
  var grid = document.querySelector('table.grid');
  if (grid) {
    var cellUrl = grid.dataset.cellUrl;
    function show(td, text) {
      td.textContent = '';
      if (td.dataset.type === 'url' && text) {
        var a = el('a', {href: text, target: '_blank', rel: 'noopener'}, text.length > 40 ? text.slice(0, 39) + '…' : text);
        td.appendChild(a);
      } else {
        td.textContent = text;
      }
    }
    function save(td, value, previousText) {
      td.classList.add('saving');
      return postJSON(cellUrl.replace('/0/', '/' + td.dataset.row + '/'), {field: td.dataset.field, value: value})
        .then(function (data) {
          td.dataset.value = JSON.stringify(data.value);
          td.classList.remove('bad');
          td.removeAttribute('data-error');
          show(td, data.text);
        })
        .catch(function (err) {
          show(td, previousText);
          td.classList.add('bad');
          td.title = err.message;
          alert(err.message);
        })
        .finally(function () { td.classList.remove('saving'); });
    }
    grid.addEventListener('dblclick', function (e) {
      var td = e.target.closest('td.editable');
      if (!td || td.querySelector('input,select')) return;
      var value = JSON.parse(td.dataset.value || 'null');
      var previousText = td.textContent.trim();
      var type = td.dataset.type;
      if (type === 'checkbox') { save(td, !value, previousText); return; }
      var input;
      if (type === 'select') {
        input = el('select');
        input.appendChild(el('option', {value: ''}, '—'));
        (td.dataset.choices || '').split('|').filter(Boolean).forEach(function (c) {
          var o = el('option', {value: c}, c);
          if (c === value) o.selected = true;
          input.appendChild(o);
        });
      } else {
        input = el('input', {type: type === 'date' ? 'date' : 'text'});
        input.value = value === null || value === undefined ? '' : value;
      }
      td.textContent = '';
      td.appendChild(input);
      input.focus();
      var done = false;
      function finish(commit) {
        if (done) return;
        done = true;
        var newValue = input.value;
        if (commit && String(newValue) !== String(value === null || value === undefined ? '' : value)) {
          save(td, newValue, previousText);
        } else {
          show(td, previousText);
        }
      }
      input.addEventListener('keydown', function (ev) {
        if (ev.key === 'Enter') { ev.preventDefault(); finish(true); }
        if (ev.key === 'Escape') { finish(false); }
      });
      input.addEventListener('blur', function () { finish(true); });
      if (type === 'select') input.addEventListener('change', function () { finish(true); });
    });
  }

  // -- Link pickers --------------------------------------------------------
  function initPicker(picker) {
    var name = picker.dataset.name, multiple = picker.dataset.multiple === '1';
    var picked = picker.querySelector('.picked'), input = picker.querySelector('.picker-input');
    var results = picker.querySelector('.picker-results'), timer = null, focus = -1;

    function changed() { picker.dispatchEvent(new CustomEvent('picker-change', {bubbles: true})); }
    function add(id, label) {
      if (picked.querySelector('[data-id="' + id + '"]')) return;
      if (!multiple) picked.innerHTML = '';
      var chip = el('span', {'class': 'chip', 'data-id': id}, label);
      chip.appendChild(el('input', {type: 'hidden', name: name, value: id}));
      chip.appendChild(el('button', {type: 'button', 'class': 'x', title: 'Remove'}, '×'));
      picked.appendChild(chip);
      changed();
    }
    picked.addEventListener('click', function (e) {
      if (e.target.classList.contains('x')) { e.target.parentNode.remove(); changed(); }
    });
    function search() {
      var url = picker.dataset.url + '?q=' + encodeURIComponent(input.value) +
        (picker.dataset.exclude ? '&exclude=' + picker.dataset.exclude : '');
      fetch(url, {credentials: 'same-origin'}).then(function (r) { return r.json(); }).then(function (data) {
        results.innerHTML = '';
        focus = -1;
        data.rows.forEach(function (row) {
          var item = el('div', {'data-id': row.id}, row.label);
          if (row.subtitle) item.appendChild(el('span', {'class': 'sub'}, row.subtitle));
          item.addEventListener('mousedown', function (e) {
            e.preventDefault();
            add(row.id, row.label);
            input.value = '';
            results.hidden = true;
          });
          results.appendChild(item);
        });
        if (!data.rows.length) results.appendChild(el('div', {'class': 'sub'}, 'Nothing found.'));
        results.hidden = false;
      });
    }
    input.addEventListener('input', function () { clearTimeout(timer); timer = setTimeout(search, 180); });
    input.addEventListener('focus', search);
    input.addEventListener('blur', function () { setTimeout(function () { results.hidden = true; }, 150); });
    input.addEventListener('keydown', function (e) {
      var items = results.querySelectorAll('div[data-id]');
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        focus = Math.max(0, Math.min(items.length - 1, focus + (e.key === 'ArrowDown' ? 1 : -1)));
        items.forEach(function (it, i) { it.classList.toggle('focus', i === focus); });
      } else if (e.key === 'Enter') {
        e.preventDefault();
        if (items[focus]) items[focus].dispatchEvent(new MouseEvent('mousedown'));
      } else if (e.key === 'Escape') {
        results.hidden = true;
      }
    });
  }
  document.querySelectorAll('.picker').forEach(initPicker);

  // -- Attribute values: inputs for the attributes of the picked category ---
  document.querySelectorAll('.attr-values').forEach(function (box) {
    var name = box.dataset.name, defs = JSON.parse(box.dataset.defs || '{}');
    var values = JSON.parse(box.dataset.values || '{}');
    var via = document.querySelector('.picker[data-name="' + box.dataset.via + '"]');
    function pickedId() {
      var hidden = via && via.querySelector('.picked input[type=hidden]');
      return hidden ? hidden.value : '';
    }
    function remember() {
      box.querySelectorAll('input[data-attr]').forEach(function (i) { values[i.dataset.attr] = i.value; });
    }
    function render() {
      remember();
      box.innerHTML = '';
      var id = pickedId();
      if (!id) { box.appendChild(el('p', {'class': 'help'}, 'Pick one above to fill in its attributes.')); return; }
      var list = defs[id] || [];
      if (!list.length) { box.appendChild(el('p', {'class': 'help'}, 'It has no attributes.')); return; }
      var table = el('table');
      list.forEach(function (d) {
        var tr = el('tr'), th = el('th', {}, d.name);
        if (d.required) th.appendChild(el('span', {'class': 'req'}, '*'));
        var current = values[d.name];
        if (current === undefined) {
          Object.keys(values).forEach(function (k) { if (k.toLowerCase() === d.name.toLowerCase()) current = values[k]; });
        }
        var input = el('input', {name: name + '::' + d.name, 'data-attr': d.name, placeholder: d.hint || ''});
        input.value = current || '';
        var td = el('td');
        td.appendChild(input);
        tr.appendChild(th);
        tr.appendChild(td);
        table.appendChild(tr);
      });
      box.appendChild(table);
    }
    if (via) via.addEventListener('picker-change', render);
    render();
  });

  // -- Attribute list editor (on categories) -------------------------------
  document.querySelectorAll('.defs-add').forEach(function (button) {
    button.addEventListener('click', function () {
      var name = button.dataset.name;
      var body = document.querySelector('.defs-editor[data-name="' + name + '"] tbody');
      var tr = el('tr');
      tr.innerHTML = '<td><input name="' + name + '__name"></td><td><input name="' + name + '__hint"></td>' +
        '<td><select name="' + name + '__req"><option value="">No</option><option value="1">Yes</option></select></td>' +
        '<td><button type="button" class="btn small row-remove">×</button></td>';
      body.appendChild(tr);
      tr.querySelector('input').focus();
    });
  });
  document.addEventListener('click', function (e) {
    if (e.target.classList.contains('row-remove')) {
      e.target.closest('tr, .filter-row').remove();
    }
  });
  document.querySelectorAll('.defs-editor').forEach(function (t) {
    if (!t.querySelector('tbody tr')) {
      var add = document.querySelector('.defs-add[data-name="' + t.dataset.name + '"]');
      if (add) add.click();
    }
  });

  // -- Field form: show the options of the chosen type ---------------------
  var typeSelect = document.getElementById('field-type');
  if (typeSelect) {
    var sync = function () {
      var type = typeSelect.value, option = typeSelect.selectedOptions[0];
      document.querySelectorAll('.opts').forEach(function (box) {
        box.hidden = box.dataset.type.split(' ').indexOf(type) < 0;
      });
      document.querySelectorAll('.type-help').forEach(function (p) { p.hidden = p.dataset.type !== type; });
      var def = document.querySelector('.opt-default');
      if (def) def.name = 'opt_' + type + '_default';
      document.getElementById('required-box').hidden = option.dataset.required !== '1';
      document.getElementById('unique-box').hidden = option.dataset.unique !== '1';
    };
    typeSelect.addEventListener('change', sync);
    sync();
  }

  // -- Copy buttons ----------------------------------------------------------
  document.querySelectorAll('button.copy').forEach(function (button) {
    button.addEventListener('click', function () {
      var target = document.getElementById(button.dataset.target);
      var text = target.value || target.textContent;
      var done = function () { var t = button.textContent; button.textContent = 'Copied ✓'; setTimeout(function () { button.textContent = t; }, 1500); };
      if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(text).then(done);
      } else {
        target.select();
        document.execCommand('copy');
        done();
      }
    });
  });

  // -- View editor ---------------------------------------------------------------
  var viewForm = document.querySelector('.view-form');
  if (viewForm) {
    var meta = JSON.parse(viewForm.dataset.fields || '{}');
    var links = JSON.parse(viewForm.dataset.links || '{}');
    viewForm.querySelectorAll('.column-list').forEach(function (list) {
      list.addEventListener('click', function (e) {
        var li = e.target.closest('li');
        if (e.target.classList.contains('up') && li.previousElementSibling) li.parentNode.insertBefore(li, li.previousElementSibling);
        if (e.target.classList.contains('down') && li.nextElementSibling) li.parentNode.insertBefore(li.nextElementSibling, li);
      });
    });
    var setupFilter = function (row) {
      var fieldSel = row.querySelector('.filter-field'), opSel = row.querySelector('.filter-op');
      var valueBox = row.querySelector('.filter-value');
      var update = function () {
        var m = meta[fieldSel.value], current = opSel.value || opSel.dataset.current;
        var currentValue = (valueBox.querySelector('[name=filter_value]') || {}).value || valueBox.dataset.current || '';
        opSel.innerHTML = '';
        (m ? m.ops : []).forEach(function (pair) {
          var o = el('option', {value: pair[0]}, pair[1]);
          if (pair[0] === current) o.selected = true;
          opSel.appendChild(o);
        });
        var needsValue = m && ['empty', 'not_empty', 'true', 'false'].indexOf(opSel.value) < 0;
        var input;
        if (m && m.type === 'link') {
          input = el('select', {name: 'filter_value'});
          (links[fieldSel.value] || []).forEach(function (pair) {
            var o = el('option', {value: pair[0]}, pair[1]);
            if (String(pair[0]) === String(currentValue)) o.selected = true;
            input.appendChild(o);
          });
        } else if (m && m.type === 'select') {
          input = el('select', {name: 'filter_value'});
          m.choices.forEach(function (c) {
            var o = el('option', {value: c}, c);
            if (c === currentValue) o.selected = true;
            input.appendChild(o);
          });
        } else {
          input = el('input', {name: 'filter_value', type: m && m.type === 'date' ? 'date' : 'text'});
          input.value = currentValue;
        }
        valueBox.innerHTML = '';
        valueBox.appendChild(input);
        valueBox.style.display = needsValue ? '' : 'none';
      };
      fieldSel.addEventListener('change', function () { opSel.dataset.current = ''; opSel.value = ''; update(); });
      opSel.addEventListener('change', update);
      update();
    };
    var filterRows = viewForm.querySelector('.filter-rows');
    var template = filterRows.lastElementChild.cloneNode(true);
    filterRows.querySelectorAll('.filter-row').forEach(setupFilter);
    document.getElementById('add-filter').addEventListener('click', function () {
      var row = template.cloneNode(true);
      filterRows.appendChild(row);
      setupFilter(row);
    });
    var sortRows = viewForm.querySelector('.sort-rows');
    var sortTemplate = sortRows.lastElementChild.cloneNode(true);
    document.getElementById('add-sort').addEventListener('click', function () {
      sortRows.appendChild(sortTemplate.cloneNode(true));
    });
  }
})();
