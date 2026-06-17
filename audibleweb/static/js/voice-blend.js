(function () {
  function all(selector, root) {
    return Array.from((root || document).querySelectorAll(selector));
  }

  function slotsForMode(mode) {
    if (mode === 'single') return 1;
    if (mode === 'weighted') return 2;
    return 3;
  }

  function fetchVoices() {
    return fetch('/api/voices')
      .then(function (resp) {
        if (!resp.ok) throw new Error('unreachable');
        return resp.json();
      })
      .then(function (data) {
        return data.voices || [];
      })
      .catch(function () {
        return null;
      });
  }

  function populatePicker(select, voices, currentValue) {
    if (!voices) {
      var input = document.createElement('input');
      input.type = 'text';
      input.className = select.className;
      input.value = currentValue || '';
      input.placeholder = 'e.g. af_heart';
      select.replaceWith(input);
      return;
    }
    select.innerHTML = '';
    var blank = document.createElement('option');
    blank.value = '';
    blank.textContent = '(none)';
    select.appendChild(blank);
    voices.forEach(function (name) {
      var opt = document.createElement('option');
      opt.value = name;
      opt.textContent = name;
      if (name === currentValue) opt.selected = true;
      select.appendChild(opt);
    });
  }

  function syncSlotVisibility(builder) {
    var checked = builder.querySelector('input[type="radio"]:checked');
    var mode = checked ? checked.value : 'single';
    var count = slotsForMode(mode);
    all('.voice-blend-slot', builder).forEach(function (slot, i) {
      slot.style.display = i < count ? '' : 'none';
      var weight = slot.querySelector('.voice-blend-weight');
      if (weight) weight.style.display = mode === 'weighted' ? '' : 'none';
    });
  }

  function updateSpec(builder) {
    var checked = builder.querySelector('input[type="radio"]:checked');
    var mode = checked ? checked.value : 'single';
    var slots = all('.voice-blend-slot', builder).slice(0, slotsForMode(mode));
    var hidden = builder.querySelector('.voice-blend-hidden');
    var names = slots
      .map(function (slot) {
        var field = slot.querySelector('select, input[type="text"]');
        return field ? field.value.trim() : '';
      })
      .filter(Boolean);

    if (mode === 'weighted' && names.length === 2) {
      var weights = slots.map(function (slot) {
        var w = slot.querySelector('.voice-blend-weight');
        return w ? parseFloat(w.value) : 0.5;
      });
      hidden.value =
        names[0] + ':' + weights[0].toFixed(2) + '+' + names[1] + ':' + weights[1].toFixed(2);
    } else {
      hidden.value = names.join('+');
    }
  }

  function linkWeightSliders(builder) {
    // The macro always renders 3 .voice-blend-weight sliders (hidden via CSS
    // outside weighted mode, never removed from the DOM) -- so querying for
    // "exactly 2 sliders" never matches. Weighted mode only ever uses the
    // first 2 slots, so grab those specifically instead of counting all of
    // them.
    var slots = all('.voice-blend-slot', builder);
    var sliderA = slots[0] && slots[0].querySelector('.voice-blend-weight');
    var sliderB = slots[1] && slots[1].querySelector('.voice-blend-weight');
    if (!sliderA || !sliderB) return;
    sliderA.addEventListener('input', function () {
      sliderB.value = (1 - parseFloat(sliderA.value)).toFixed(2);
      updateSpec(builder);
    });
    sliderB.addEventListener('input', function () {
      sliderA.value = (1 - parseFloat(sliderB.value)).toFixed(2);
      updateSpec(builder);
    });
  }

  function bindEvents(builder) {
    all('input[type="radio"]', builder).forEach(function (radio) {
      radio.addEventListener('change', function () {
        syncSlotVisibility(builder);
        updateSpec(builder);
      });
    });
    // 'change' alone misses the Kokoro-offline fallback text input: text
    // inputs only fire 'change' on blur, so typing a voice then submitting
    // via Enter (no blur) would leave the hidden field stale. 'input' fires
    // per keystroke and is harmless to also listen for on a <select>.
    ['change', 'input'].forEach(function (evt) {
      builder.addEventListener(evt, function (e) {
        if (e.target.classList.contains('voice-blend-picker')) updateSpec(builder);
      });
    });
    linkWeightSliders(builder);
  }

  function initBuilder(builder) {
    var pickers = all('.voice-blend-picker', builder);
    var currentValues = pickers.map(function (s) {
      return s.dataset.initial || '';
    });

    fetchVoices().then(function (voices) {
      pickers.forEach(function (select, i) {
        populatePicker(select, voices, currentValues[i]);
      });
      bindEvents(builder);
      syncSlotVisibility(builder);
      updateSpec(builder);
    });
  }

  function initAll(root) {
    all('.voice-blend-builder', root).forEach(initBuilder);
  }

  document.addEventListener('DOMContentLoaded', function () {
    initAll(document);
  });
  document.addEventListener('htmx:afterSettle', function (e) {
    if (e.detail && e.detail.target) initAll(e.detail.target);
  });
})();
