/* Combat Lab web client.
   The browser owns the editable draft.  The local Python service only receives
   a normalized plan when a plan is saved or an evaluation is started. */

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));
let cardSerial = 0;
let relicSerial = 0;

function newCard(id, values = {}) {
  cardSerial += 1;
  const card = {
    uid: values.uid || `card-${cardSerial}`,
    id,
    upgrade: Number(values.upgrade ?? values.upgradeLevel ?? values.upgradeLevels ?? 0),
    enchantmentId: values.enchantmentId || '',
    enchantmentAmount: values.enchantmentAmount ?? '',
    ref: values.ref || undefined,
    savedProps: structuredClone(values.savedProps || {}),
    dynamicVars: structuredClone(values.dynamicVars || {}),
    propsEdited: !!values.propsEdited,
    defaultedProps: structuredClone(values.defaultedProps || []),
    floorAdded: values.floorAdded,
  };
  const schema = id === 'MAD_SCIENCE' ? state.catalog?.cards?.[id]?.configuration : null;
  if (schema) {
    for (const [property, choices] of [[schema.typeProperty, schema.types], [schema.riderProperty, schema.riders]]) {
      const serialized = values.enumMembers?.[property];
      if (serialized != null && !card.savedProps.ints?.some((p) => p.name === property)) {
        const choice = choices.find((entry) => entry.enum.toLowerCase() === String(serialized).toLowerCase() || String(entry.value) === String(serialized));
        setCardProperty(card, property, choice?.value ?? (String(serialized).toLowerCase() === 'none' ? 0 : Number(serialized)));
      }
    }
    if (!values.ref && !('savedProps' in values) && !values.enumMembers) {
      setCardProperty(card, schema.typeProperty, schema.types[0].value);
      setCardProperty(card, schema.riderProperty, schema.riders.find((rider) => rider.typeValue === schema.types[0].value).value);
    }
  }
  const growth = ['THE_SCYTHE', 'GENETIC_ALGORITHM'].includes(id) ? state.catalog?.cards?.[id]?.growth : null;
  if (growth) {
    const current = (card.savedProps.ints || []).find((field) => field.name === growth.current);
    const increased = (card.savedProps.ints || []).find((field) => field.name === growth.increased);
    if (current && !increased) setCardProperty(card, growth.increased, current.value - growth.base);
    if (increased && !current) setCardProperty(card, growth.current, growth.base + increased.value);
    if (Number.isFinite(card.dynamicVars[growth.var])) {
      setCardGrowth(card, card.dynamicVars[growth.var]); delete card.dynamicVars[growth.var];
    } else if (!current && !increased && !card.defaultedProps.some((field) => [growth.current, growth.increased].includes(field.name))) {
      setCardGrowth(card, growth.base);
    }
  }
  return card;
}

function setCardProperty(card, name, value) {
  const fields = (card.savedProps.ints ||= []), previous = fields.find((entry) => entry.name === name);
  if (previous) previous.value = value; else fields.push({ name, value });
  card.defaultedProps = card.defaultedProps.filter((property) => property.name !== name);
  card.propsEdited = true;
}

function newRelic(id, values = {}) {
  relicSerial += 1;
  const savedProps = structuredClone(values.savedProps || {});
  for (const [key, group] of [['integerMembers', 'ints'], ['booleanMembers', 'bools']]) {
    for (const [name, value] of Object.entries(values[key] || {})) {
      if (!(savedProps[group] || []).some((p) => p.name === name)) (savedProps[group] ||= []).push({ name, value });
    }
  }
  const propsEdited = !!values.propsEdited || !!values.integerMembers || !!values.booleanMembers;
  const propertySources = { ...values.propertySources };
  if (propsEdited) for (const group of ['ints', 'bools', 'strings']) for (const property of savedProps[group] || []) propertySources[property.name] = 'manual';
  return { uid: values.uid || `relic-${relicSerial}`, id, ref: values.ref,
    savedProps, propsEdited,
    defaultedProps: structuredClone(values.defaultedProps || []),
    propertySources, floorAdded: values.floorAdded };
}

const state = {
  catalog: null,
  language: 'zhs',
  tab: 'build',
  picker: { kind: null },
  mode: 'build',
  character: 'IRONCLAD',
  seed: 'COMBAT-LAB-IRONCLAD',
  ascension: 10,
  act: 0,
  actId: 'OVERGROWTH',
  save: { path: '', localPath: '', files: [], bundle: null, drafts: new Map(), busy: false, dirty: false, error: '' },
  playerStats: { hp: 80, maxHp: 80, gold: 99, maxEnergy: 3, potionSlots: 2, orbSlots: 0 },
  inventoryRefs: { relic: {}, potion: {} },
  deck: [
    ...Array.from({ length: 5 }, () => newCard('STRIKE_IRONCLAD')),
    ...Array.from({ length: 4 }, () => newCard('DEFEND_IRONCLAD')),
    newCard('BASH', { upgrade: 1 }),
  ],
  relics: [newRelic('BURNING_BLOOD')],
  potions: {},
  direct: ['FUZZY_WURM_CRAWLER_WEAK'],
  pool: [],
  encounterSource: 'direct',
  selectedEncounters: new Set(),
  encounterFilters: { search: '', stage: '0', branch: 'OVERGROWTH', mode: 'group', groupTier: 'weak', poolPreset: 'CURRENT_WEAK' },
  jobId: null,
  pollTimer: null,
  report: null,
  filters: { deckQuery: '', cardQuery: '', cardType: 'ALL', cardRarity: 'ALL', cardCost: 'ALL', cardPool: 'CURRENT', relicQuery: '', relicRarity: 'ALL', potionQuery: '', potionRarity: 'ALL' },
  settings: { profile: 'Medium', searchMode: 'Evaluate', potionPolicy: 'Smart', language: 'zhs', samples: 3, poolSeed: 'pool-a', budgetMs: 120000, dop: 2, caseWorkers: 2, beam: '', nodes: '', cardBranches: '', pileBranches: '', handBranches: '', stopAtZeroLoss: false, productionBudget: false, measurePhases: false },
};

const zh = {
  build: ['状态构建', '构建你的战斗状态', '在这里编辑逐张卡牌、遗物和药水；其他页面只显示当前状态摘要。'],
  encounter: ['遭遇与采样', '选择测试遭遇', '先确定阶段与分支，再选择当前怪物组或随机池。数据来自 SeedOracle 的弱怪、强怪、精英和 Boss 池。'],
  settings: ['求解设置', '控制搜索预算', '保持预算与样本规模一致，比较不同卡组时结果才有可比性。'],
  results: ['评估结果', '查看模拟结果', '查看每个遭遇样本的战损、完成率、最终生命与用药情况。'],
};
const en = {
  build: ['Build state', 'Build your combat state', 'Edit each card instance, relic and potion here. Other pages show a read-only summary.'],
  encounter: ['Encounters', 'Choose test encounters', 'Choose act and branch first, then a current monster group or random pool.'],
  settings: ['Solver settings', 'Control search budget', 'Keep the budget and sample count consistent when comparing decks.'],
  results: ['Evaluation', 'Review simulation results', 'Inspect damage, completion, final HP and potion use for every sample.'],
};

function textFor(data) {
  if (!data) return '';
  return state.language === 'eng' ? (data.name_en || data.name || data.id) : (data.name || data.name_en || data.id);
}
function descriptionFor(data) {
  if (!data) return '';
  return state.language === 'eng' ? (data.description_en || data.description || '') : (data.description || data.description_en || '');
}
function plainDescription(data) {
  let value = descriptionFor(data)
    .replace(/\[\/?[^\]]+\]/g, '')
    .replace(/\{([^}:]+)(?::[^}]+)?\}/g, '$1')
    .replace(/\s+/g, ' ')
    .trim();
  if (state.language !== 'eng') {
    const terms = { Damage: '伤害', Block: '格挡', Energy: '能量', Strength: '力量', Dexterity: '敏捷', Vulnerable: '易伤', Weak: '虚弱', Poison: '中毒', Heal: '生命', HP: '生命', MaxHP: '最大生命', Draw: '抽牌', Discard: '弃牌', Exhaust: '消耗', Power: '能力', Gold: '金币', Increase: '增加', Reduce: '减少', Prefix: '前缀', Scry: '预见' };
    for (const [from, to] of Object.entries(terms)) value = value.replace(new RegExp(`\\b${from}\\b`, 'g'), to);
    value = value.replace(/伤害点伤害/g, '伤害').replace(/增加增加/g, '增加').replace(/格挡点格挡/g, '格挡');
  }
  return value;
}
function assetUrl(itemData) {
  const path = itemData?.image || '';
  if (!path) return '';
  const url = '/' + path.replace(/^\/?combat_lab\/assets\//, 'assets/').replace(/^\/+/, '');
  return `${url}?art=${encodeURIComponent(state.catalog?.assetVersion || 1)}`;
}
function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[char]));
}
function item(kind, id) { return state.catalog?.[kind]?.[id] || { id, name: id, name_en: id }; }
function allItems(kind) { return Object.values(state.catalog?.[kind] || {}); }
function rarityLabel(code, kind = 'card') {
  const group = kind === 'card' ? 'cardRarities' : kind === 'relic' ? 'relicRarities' : 'potionRarities';
  return state.catalog?.ui?.[group]?.[code] || code || '—';
}
function typeLabel(code) { return state.catalog?.ui?.cardTypes?.[code] || code || '—'; }
function formatCount(count, unit) {
  return `${count} ${state.language === 'eng' ? unit : ({ card: '张', relic: '件', potion: '瓶', encounter: '场' }[unit] || unit)}`;
}
function showToast(message, timeout = 3000) {
  const el = $('#toast');
  if (!el) return;
  el.textContent = message; el.hidden = false;
  clearTimeout(showToast.timer); showToast.timer = setTimeout(() => { el.hidden = true; }, timeout);
}
function setupSelect(select, options, selected, label = (value) => value) {
  if (!select) return;
  select.innerHTML = options.map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(label(value))}</option>`).join('');
  if (selected != null && options.includes(String(selected))) select.value = String(selected);
}
function currentAct() { return (state.catalog?.acts || []).find((act) => Number(act.index) === Number(state.act)) || state.catalog?.acts?.[0]; }
function currentVariant() { return (currentAct()?.variants || []).find((variant) => variant.id === state.actId) || currentAct()?.variants?.[0]; }
function actLabel(index) { return state.language === 'eng' ? `Act ${Number(index) + 1}` : `第 ${Number(index) + 1} 幕`; }
function variantLabel(variant) {
  if (!variant) return '—';
  if (typeof variant === 'string') {
    const found = (state.catalog?.acts || []).flatMap((act) => act.variants || []).find((candidate) => candidate.id === variant);
    return found ? textFor(found) : variant;
  }
  return textFor(variant);
}
function canEditState() { return !state.save.busy && (state.mode === 'build' || !!state.save.bundle); }

function markEdited() {
  if (state.save.bundle) state.save.dirty = true;
  $('#saveDraftBadge').hidden = !state.save.dirty;
  $('#resetFloorButton').disabled = !state.save.dirty || state.save.busy;
}

function selectionKey(selection) { return `${selection.player}:${selection.floor}:${selection.point}`; }
function captureDraft() {
  return structuredClone({ deck: state.deck, relics: state.relics, potions: state.potions, playerStats: state.playerStats,
    inventoryRefs: state.inventoryRefs, character: state.character, seed: state.seed, ascension: state.ascension,
    act: state.act, actId: state.actId, dirty: state.save.dirty });
}
function storeFloorDraft() {
  if (state.save.bundle) state.save.drafts.set(selectionKey(state.save.bundle.selection), captureDraft());
}

async function saveApi(path, body) {
  const response = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || '存档读取失败');
  return data;
}

async function refreshSaves() {
  $('#refreshSavesButton').disabled = true;
  try {
    const response = await fetch('/api/saves'); const data = await response.json();
    if (!response.ok) throw new Error(data.error || '查找存档失败');
    state.save.files = data.items || [];
    renderLocalSaves();
  } catch (error) { showToast(error.message, 5000); }
  finally { $('#refreshSavesButton').disabled = false; }
}

function renderLocalSaves() {
  const options = ['<option value="">选择存档或历史记录…</option>'];
  for (const [kind, title] of [['current', '进行中的存档'], ['history', '历史记录']]) {
    const files = state.save.files.filter((file) => file.kind === kind);
    if (!files.length) continue;
    options.push(`<optgroup label="${title}">${files.map((file) => {
      const date = new Date(file.modifiedAt * 1000).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
      const label = `${textFor(item('characters', file.character))} · A${file.ascension} · ${file.floors} 层 · ${date} · ${file.profile}`;
      return `<option value="${escapeHtml(file.path)}">${escapeHtml(label)}</option>`;
    }).join('')}</optgroup>`);
  }
  $('#localSaveSelect').innerHTML = options.join('');
  $('#localSaveSelect').value = state.save.localPath;
}

function applyImportedState(bundle, { resetDrafts = false, draft = null } = {}) {
  if (resetDrafts) state.save.drafts.clear();
  state.save.bundle = bundle; state.save.dirty = false; state.mode = 'save';
  state.character = bundle.character; state.seed = bundle.seed; state.ascension = Number(bundle.ascension);
  state.act = Number(bundle.act); state.actId = bundle.actId; state.playerStats = { ...bundle.stats };
  state.deck = bundle.deck.map((card) => newCard(card.id, card));
  state.relics = bundle.relics.map((value) => newRelic(value.id, value));
  state.potions = {}; state.inventoryRefs = { relic: {}, potion: {} };
  for (const value of bundle.potions) {
    state.potions[value.id] = (state.potions[value.id] || 0) + 1;
    (state.inventoryRefs.potion[value.id] ||= []).push(structuredClone(value));
  }
  const restoredDraft = draft || state.save.drafts.get(selectionKey(bundle.selection));
  if (restoredDraft) {
    const { dirty, ...values } = structuredClone(restoredDraft); Object.assign(state, values); state.save.dirty = dirty;
  }
  syncEncounterContext(); renderAll();
}

async function importSave(body) {
  if (state.save.busy) return;
  state.save.busy = true; state.save.error = ''; renderSaveImport();
  try {
    const bundle = await saveApi('/api/saves/import', body);
    state.save.path = bundle.source.path || '';
    state.save.busy = false; applyImportedState(bundle, { resetDrafts: true });
    showToast(`已读取 ${bundle.source.name}，${bundle.deck.length} 张牌`);
  } catch (error) { state.save.error = error.message; }
  finally { state.save.busy = false; renderAll(); }
}

async function selectSaveFloor(changes, { reset = false } = {}) {
  if (!state.save.bundle || state.save.busy) return;
  const bundle = state.save.bundle;
  if (!reset) storeFloorDraft();
  const selection = { ...bundle.selection, ...changes };
  if ('floor' in changes && !('point' in changes) && selection.point === 'current') selection.point = 'before';
  if (reset) state.save.drafts.delete(selectionKey(selection));
  state.save.busy = true; state.save.error = ''; renderSaveImport();
  try {
    const data = await saveApi('/api/saves/state', { sourceId: bundle.source.id, selection });
    state.save.busy = false; applyImportedState(data);
  } catch (error) { state.save.error = error.message; }
  finally { state.save.busy = false; renderAll(); }
}

const roomLabels = { monster: '普通战斗', elite: '精英', boss: 'Boss', unknown: '事件', rest_site: '休息站', treasure: '宝箱', shop: '商店', ancient: '先古', unassigned: '房间' };
const actionLabels = { cards_gained: '获得卡牌', cards_removed: '移除卡牌', upgraded_cards: '强化', downgraded_cards: '降级',
  relics_gained: '获得遗物', relics_removed: '移除遗物', potions_gained: '获得药水', potion_used: '使用药水', potion_discarded: '丢弃药水',
  rest_site_choices: '休息站选择', event_choices: '事件选择', ancient_choice: '先古选择', transform: '变形', enchant: '附魔' };
function floorRoom(row) { return row?.encounters?.length ? row.encounters.map((id) => textFor(item('encounters', id))).join(' / ') : roomLabels[row?.room] || row?.room || '—'; }
function actionText(action, compact = false) {
  const label = actionLabels[action.kind] || action.kind;
  if (compact) return `${label} ${action.count || action.ids?.length || 1}`;
  if (action.kind === 'rest_site_choices') return (action.choices || []).map((choice) => ({ HEAL: '休息', SMITH: '强化', DIG: '挖掘', LIFT: '锻炼' }[choice] || label)).join('、');
  if (!action.ids) return `${label} ×${action.count || 1}`;
  const group = action.kind.includes('relic') ? 'relics' : action.kind.includes('potion') ? 'potions' : 'cards';
  const names = action.ids.map((id, i) => textFor(item(action.kind === 'enchant' && i === 1 ? 'enchantments' : group, id)));
  return `${label}：${names.join(action.kind === 'transform' || action.kind === 'enchant' ? ' → ' : '、')}`;
}

function renderSaveImport() {
  const save = state.save, bundle = save.bundle;
  $('#saveError').hidden = !save.error; $('#saveError').textContent = save.error;
  $('#saveSourceLabel').textContent = save.busy ? '正在读取楼层状态…' : bundle ? `${bundle.source.name} · ${bundle.source.kind === 'current' ? '当前存档' : '历史记录'} · ${bundle.source.floors} 层` : '选择进行中的存档或历史记录';
  $('#saveSourceLabel').title = bundle?.source.path || '';
  $('#saveDraftBadge').hidden = !save.dirty;
  for (const id of ['importSaveButton', 'importPathButton', 'uploadSaveButton', 'localSaveSelect']) $(`#${id}`).disabled = save.busy;
  $('#floorNavigator').hidden = !bundle;
  if (!bundle) return;
  const { selection, history, source } = bundle;
  $('#savePlayerField').hidden = source.players.length < 2;
  setupSelect($('#savePlayerSelect'), source.players.map((p) => String(p.index)), String(selection.player), (index) => `玩家 ${Number(index) + 1} · ${textFor(item('characters', source.players[Number(index)].character))}`);
  $('#floorInput').value = selection.floor; $('#floorInput').max = source.floors; $('#floorInput').disabled = save.busy || !source.floors;
  setupSelect($('#floorSelect'), history.map((row) => String(row.floor)), String(selection.floor), (floor) => { const row = history[Number(floor) - 1]; return `第 ${floor} 层 · ${variantLabel(row.actId)} · ${floorRoom(row)}`; });
  $('#floorSelect').disabled = save.busy || !source.floors;
  $('#previousFloor').disabled = save.busy || selection.floor <= 1;
  $('#nextFloor').disabled = save.busy || selection.floor >= source.floors;
  $$('[data-save-point]').forEach((button) => {
    button.classList.toggle('active', button.dataset.savePoint === selection.point);
    button.setAttribute('aria-pressed', button.dataset.savePoint === selection.point);
    button.disabled = save.busy || (button.dataset.savePoint === 'current' ? source.kind !== 'current' : !source.floors);
  });
  $('#currentPointButton').hidden = source.kind !== 'current';
  $('#restoreSourceButton').disabled = save.busy; $('#resetFloorButton').disabled = save.busy || !save.dirty;
  const row = bundle.selectedFloor;
  const point = { before: '进入本层前', after: '完成本层后', current: '存档当前状态' }[selection.point];
  $('#selectedFloorTitle').textContent = `${selection.point === 'current' ? '' : `第 ${selection.floor} 层 · `}${point} · ${variantLabel(bundle.actId)}${row ? ` · ${floorRoom(row)}` : ''}`;
  $('#selectedFloorStats').textContent = `生命 ${state.playerStats.hp}/${state.playerStats.maxHp} · 金币 ${state.playerStats.gold} · ${state.deck.length} 张牌 · ${state.relics.length} 件遗物 · ${save.dirty ? '已手动调整' : '读取完成'}`;
  $('#useFloorEncounter').disabled = save.busy || !row?.encounters?.length;
  const actions = row?.actions || [];
  $('#floorActions').innerHTML = '<span class="floor-actions-caption">本层记录</span>' + (actions.length ? actions.map((action) => `<span class="floor-action">${escapeHtml(actionText(action))}</span>`).join('') : '<span class="floor-no-actions">没有记录物品变化</span>');
  $('#historyCount').textContent = `${history.length} 层`;
  $('#floorHistoryRows').innerHTML = history.map((entry) => `<tr class="${entry.floor === selection.floor ? 'selected' : ''}"><td><button class="floor-row-button" data-history-floor="${entry.floor}" ${save.busy ? 'disabled' : ''}>第 ${entry.floor} 层</button></td><td>${escapeHtml(variantLabel(entry.actId))}<small>${escapeHtml(floorRoom(entry))}</small></td><td>${entry.hp ?? '—'}/${entry.maxHp ?? '—'}</td><td>${entry.gold ?? '—'}</td><td class="history-action-cell">${escapeHtml(entry.actions.map((action) => actionText(action, true)).join(' · ') || '—')}</td></tr>`).join('');
  const issues = [...(bundle.issues || [])];
  const pendingRelics = state.relics.filter((entry) => relicFields(entry).some((field) => fieldIsUnknown(entry, field))).length;
  if (pendingRelics) issues.push(`${pendingRelics} 件遗物有未记录的计数或触发状态，可在“编辑状态”中指定；未指定时使用游戏初始值。`);
  $('#saveIssues').hidden = !issues.length;
  $('#saveIssuesTitle').textContent = `待核对 ${issues.length} 项`;
  $('#saveIssueList').innerHTML = issues.map((message) => `<li>${escapeHtml(message)}</li>`).join('');
}

function relicFields(target) {
  const schemas = state.catalog?.relicStateFields || {};
  const fields = [...(schemas.relics?.[target.id] || []), ...(schemas.common || [])].map((field) => ({ ...field }));
  for (const group of ['ints', 'bools', 'strings']) for (const property of target.savedProps?.[group] || []) {
    if (!fields.some((f) => f.name === property.name && f.group === group)) fields.push({ group, name: property.name, label: property.name });
  }
  for (const property of target.defaultedProps || []) {
    if (!fields.some((f) => f.name === property.name && f.group === property.group)) fields.push({ ...property, label: property.name });
  }
  return fields;
}
function propValue(props, field) { return (props[field.group] || []).find((entry) => entry.name === field.name)?.value ?? field.default; }
function propLabel(field) { return state.language === 'eng' ? field.label_en || field.label || field.name : field.label || field.name; }
function fieldIsUnknown(target, field, props = target.savedProps) {
  if (field.requires && !(props.bools || []).find((p) => p.name === field.requires)?.value) return false;
  return !(props[field.group] || []).some((p) => p.name === field.name) && (target.propertySources?.[field.name] === 'unknown' || target.defaultedProps?.some((p) => p.name === field.name && p.group === field.group));
}
function stateSourceLabel(source) {
  return (state.language === 'eng'
    ? { save: 'Saved value', history: 'Known from history', unknown: 'Unrecorded · using initial value', manual: 'Specified', default: 'Initial value' }
    : { save: '存档已记录', history: '按记录还原', unknown: '未记录 · 暂用初始值', manual: '手动指定', default: '初始值' })[source || 'default'];
}
function relicStateSummary(target) {
  const fields = relicFields(target), props = target.savedProps || {}, badges = [];
  const wax = fields.find((f) => f.name === 'IsWax'), melted = fields.find((f) => f.name === 'IsMelted');
  if (wax && propValue(props, wax)) badges.push({ text: state.language === 'eng' ? 'Wax' : '蜡制', tone: 'wax' });
  if (melted && propValue(props, melted)) badges.push({ text: state.language === 'eng' ? 'Melted · inactive' : '已融化 · 失效', tone: 'inactive' });
  let unknown = 0;
  for (const field of fields) {
    if (field.requires && !propValue(props, fields.find((f) => f.name === field.requires))) continue;
    if (fieldIsUnknown(target, field)) { unknown += 1; continue; }
    if (field.name === 'IsWax' || field.name === 'IsMelted') continue;
    const value = propValue(props, field);
    if (field.group === 'ints') badges.push({ text: field.cycle ? `${state.language === 'eng' ? 'Progress' : '进度'} ${value % field.cycle}/${field.cycle}` : `${propLabel(field)} ${value}`, tone: 'counter' });
    else if (field.group === 'bools') badges.push({ text: field[`${value ? 'trueLabel' : 'falseLabel'}${state.language === 'eng' ? '_en' : ''}`] || `${propLabel(field)} ${value}`, tone: value ? 'changed' : 'available' });
  }
  if (unknown) badges.push({ text: state.language === 'eng' ? `${unknown} unspecified` : `${unknown} 项待指定`, tone: 'unknown' });
  return badges.map((b) => `<span class="relic-state-badge ${b.tone}">${escapeHtml(b.text)}</span>`).join('');
}
function renderRelicPropFields() {
  const props = JSON.parse($('#savedPropsJson').value), target = state.propsTarget, fields = relicFields(target);
  $('#savedPropFields').innerHTML = fields.map((field) => {
    const value = propValue(props, field);
    const source = state.propsSources[field.name] || (fieldIsUnknown(target, field, props) ? 'unknown' : 'default');
    const disabled = field.requires && !propValue(props, fields.find((f) => f.name === field.requires));
    const attrs = `data-saved-prop-group="${field.group}" data-saved-prop-name="${escapeHtml(field.name)}" ${disabled ? 'disabled' : ''}`;
    const control = field.group === 'bools'
      ? `<input type="checkbox" role="switch" ${attrs} ${value ? 'checked' : ''}><span class="state-switch-track" aria-hidden="true"></span>`
      : `<input type="${field.group === 'strings' ? 'text' : 'number'}" ${attrs} value="${escapeHtml(value ?? '')}" ${field.group === 'ints' ? `step="1" min="${field.min ?? 0}" max="${field.max ?? 2147483647}"` : ''}>`;
    const currentLabel = field.group === 'bools' ? field[`${value ? 'trueLabel' : 'falseLabel'}${state.language === 'eng' ? '_en' : ''}`] : field.cycle ? `${state.language === 'eng' ? 'Progress' : '当前进度'} ${Number(value || 0) % field.cycle}/${field.cycle}` : '';
    return `<div class="relic-prop-field ${disabled ? 'disabled' : ''}"><label class="${field.group === 'bools' ? 'state-switch' : ''}"><span>${escapeHtml(propLabel(field))}</span>${control}</label><div class="relic-prop-info"><span>${escapeHtml(currentLabel || '')}</span><small class="prop-origin ${source}">${escapeHtml(stateSourceLabel(source))}</small></div>${source === 'unknown' && !disabled ? `<button type="button" class="mini-button confirm-prop" data-confirm-prop="${escapeHtml(field.name)}">${state.language === 'eng' ? 'Use this value' : '指定当前值'}</button>` : ''}</div>`;
  }).join('');
}
function validateRelicProps(target, props) {
  const fields = relicFields(target);
  for (const group of ['ints', 'bools', 'strings']) {
    if (props[group] != null && !Array.isArray(props[group])) throw new Error(`${group} 必须是属性列表`);
    const names = new Set();
    for (const property of props[group] || []) {
      if (!property || typeof property.name !== 'string' || names.has(property.name)) throw new Error('属性名缺失或重复');
      names.add(property.name);
      const field = fields.find((f) => f.group === group && f.name === property.name);
      if (group === 'bools' && typeof property.value !== 'boolean') throw new Error(`${propLabel(field || property)} 必须是开关值`);
      if (group === 'ints' && (!Number.isInteger(property.value) || property.value < (field?.min ?? -2147483648) || property.value > (field?.max ?? 2147483647))) throw new Error(`${propLabel(field || property)} 超出允许范围`);
    }
  }
  if (propValue(props, { group: 'bools', name: 'IsMelted' }) && !propValue(props, { group: 'bools', name: 'IsWax' })) throw new Error('只有蜡制遗物可以设置为已融化');
}
function openSavedProps(target, title, kind = 'card') {
  if (!target) return;
  state.propsTarget = target; state.propsKind = kind; state.propsSources = { ...target.propertySources };
  $('#savedPropsTitle').textContent = `${title} · ${state.language === 'eng' ? (kind === 'relic' ? 'Relic state' : 'Saved properties') : (kind === 'relic' ? '遗物状态' : '保存属性')}`;
  const props = target.savedProps || {};
  $('#savedPropsJson').value = JSON.stringify(props, null, 2); $('#savedPropsError').hidden = true;
  $('#savedPropsIdentity').hidden = kind !== 'relic';
  $('#savedPropsIdentity').innerHTML = kind === 'relic' ? `${imageMarkup(item('relics', target.id))}<div><b>${escapeHtml(title)}</b><span>${escapeHtml(rarityLabel(item('relics', target.id).rarity, 'relic'))}${target.floorAdded != null ? ` · 第 ${target.floorAdded} 层获得` : ''}</span><div class="relic-state-summary">${relicStateSummary(target)}</div></div>` : '';
  if (kind === 'relic') { renderRelicPropFields(); $('#savedPropsDialog').showModal(); return; }
  renderCardPropFields();
  $('#savedPropsDialog').showModal();
}
function renderCardPropFields() {
  const target = state.propsTarget, props = JSON.parse($('#savedPropsJson').value);
  const fields = [];
  const config = cardConfiguration(target, props);
  const growth = cardGrowth(target, props);
  if (growth) {
    fields.push(`<label>${escapeHtml(textFor(growth.schema))}<input type="number" step="1" min="${growth.schema.base}" max="${growth.schema.max}" required value="${growth.value ?? ''}" placeholder="${state.language === 'eng' ? 'Set value' : '待指定'}" data-saved-prop-group="ints" data-saved-prop-name="${growth.schema.current}" data-growth-prop="true"><small>${state.language === 'eng' ? 'Accumulated growth is synchronized automatically.' : '累计成长自动同步，无需分别填写。'}</small></label>`);
  }
  if (config) {
    $('#savedPropsIdentity').hidden = false;
    $('#savedPropsIdentity').innerHTML = `${imageMarkup(cardDisplayData({ ...target, savedProps: props }))}<div><b>${escapeHtml(textFor(item('cards', target.id)))}</b><span>${escapeHtml(config.type ? typeLabel(config.type.type) : '类型待指定')}${config.rider ? ` · ${escapeHtml(textFor(config.rider))}` : ''}</span></div>`;
    fields.push(`<label>卡牌类型<select data-saved-prop-group="ints" data-saved-prop-name="${config.schema.typeProperty}">${!config.type ? '<option value="" selected>请选择类型</option>' : ''}${config.schema.types.map((type) => `<option value="${type.value}" ${config.typeValue === type.value ? 'selected' : ''}>${escapeHtml(typeLabel(type.type))}</option>`).join('')}</select></label>`);
    fields.push(`<label>附加效果<select data-saved-prop-group="ints" data-saved-prop-name="${config.schema.riderProperty}">${config.riderValue === 0 ? '<option value="0" selected>无附加效果</option>' : ''}${config.schema.riders.filter((rider) => rider.typeValue === config.typeValue).map((rider) => `<option value="${rider.value}" ${config.riderValue === rider.value ? 'selected' : ''}>${escapeHtml(textFor(rider))}</option>`).join('')}</select></label>`);
  }
  for (const group of ['ints', 'floats', 'bools', 'strings']) (props[group] || []).forEach((property, index) => {
    if (growth && group === 'ints' && [growth.schema.current, growth.schema.increased].includes(property.name)) return;
    if (config && group === 'ints' && [config.schema.typeProperty, config.schema.riderProperty].includes(property.name)) return;
    const type = group === 'bools' ? 'checkbox' : group === 'strings' ? 'text' : 'number';
    fields.push(`<label>${escapeHtml(property.name)}<input data-saved-prop-group="${group}" data-saved-prop-name="${escapeHtml(property.name)}" type="${type}" ${group === 'floats' ? 'step="any"' : ''} ${type === 'checkbox' ? (property.value ? 'checked' : '') : `value="${escapeHtml(property.value)}"`}></label>`);
  });
  for (const property of target.defaultedProps || []) {
    if (growth && property.group === 'ints' && [growth.schema.current, growth.schema.increased].includes(property.name)) continue;
    if (config && property.group === 'ints' && [config.schema.typeProperty, config.schema.riderProperty].includes(property.name)) continue;
    if ((props[property.group] || []).some((p) => p.name === property.name)) continue;
    const control = property.group === 'bools'
      ? '<select><option value="">游戏默认</option><option value="true">启用</option><option value="false">禁用</option></select>'
      : '<input type="number" placeholder="游戏默认" step="any">';
    fields.push(`<label>${escapeHtml(property.name)}${control.replace(/<(input|select)/, `<$1 data-saved-prop-group="${property.group}" data-saved-prop-name="${escapeHtml(property.name)}" data-defaulted-prop="true"`)}</label>`);
  }
  $('#savedPropFields').innerHTML = fields.join('') || '<p>没有单独记录数值计数</p>';
}

function inventoryPlan(kind) {
  if (kind === 'relic') return state.relics.map((entry) => ({ id: entry.id, addWithoutObtainedEffects: true,
    ...(entry.ref ? { ref: entry.ref } : {}), ...(entry.propsEdited ? { savedProps: structuredClone(entry.savedProps) } : {}) }));
  const entries = Object.entries(state[`${kind}s`]).flatMap(([id, count]) => Array.from({ length: count }, (_, index) => {
    const original = state.inventoryRefs[kind][id]?.[index];
    return { id, ...(original?.ref ? { ref: original.ref } : {}), ...(original?.propsEdited ? { savedProps: original.savedProps } : {}), ...(kind === 'potion' && original?.slot != null ? { slot: original.slot } : {}) };
  }));
  if (kind === 'potion') {
    const used = new Set(entries.filter((p) => p.slot != null).map((p) => p.slot));
    for (const potion of entries) if (potion.slot == null) { let slot = 0; while (used.has(slot)) slot += 1; potion.slot = slot; used.add(slot); }
  }
  return entries;
}

function setTab(tab) {
  state.tab = tab;
  $$('.nav-item').forEach((button) => button.classList.toggle('active', button.dataset.tab === tab));
  $$('.tab-panel').forEach((panel) => panel.classList.toggle('active', panel.id === `tab-${tab}`));
  const copy = (state.language === 'eng' ? en : zh)[tab] || zh.build;
  if ($('#breadcrumb')) $('#breadcrumb').textContent = copy[0];
  if ($('#pageTitle')) $('#pageTitle').textContent = copy[1];
  if ($('#pageSubtitle')) $('#pageSubtitle').textContent = copy[2];
  renderContext();
}

function renderStateSummary() {
  const selection = state.save.bundle?.selection;
  const modeText = selection ? (selection.point === 'current' ? '存档当前' : `第 ${selection.floor} 层${selection.point === 'before' ? '前' : '后'}`) + (state.save.dirty ? ' · 已调整' : '') : '直接构建';
  const deckText = formatCount(state.deck.length, 'card');
  const relicText = formatCount(state.relics.length, 'relic');
  const potionText = formatCount(Object.values(state.potions).reduce((a, b) => a + b, 0), 'potion');
  const chips = [
    ['状态', modeText], ['角色', textFor(item('characters', state.character))], ['飞升', `A${state.ascension}`],
    ['位置', `${actLabel(state.act)} · ${variantLabel(currentVariant())}`], ['牌组', deckText], ['遗物', relicText], ['药水', potionText],
    ['生命 / 金币', `${state.playerStats.hp}/${state.playerStats.maxHp} · ${state.playerStats.gold}`],
  ];
  const target = $('#contextSummary');
  if (target) target.innerHTML = chips.map(([label, value]) => `<span class="context-chip"><small>${escapeHtml(label)}</small><b>${escapeHtml(value)}</b></span>`).join('');
}

function syncEncounterContext() {
  state.encounterFilters.stage = String(state.act);
  state.encounterFilters.branch = state.actId;
}

function renderContext() {
  if (!state.catalog) return;
  const characters = Object.values(state.catalog.characters || {});
  setupSelect($('#characterSelect'), characters.map((x) => x.id), state.character, (id) => textFor(item('characters', id)));
  setupSelect($('#ascensionSelect'), Array.from({ length: Number(state.catalog.ascensionMax ?? 10) + 1 }, (_, i) => String(i)), String(state.ascension), (value) => `A${value}`);
  setupSelect($('#actSelect'), (state.catalog.acts || []).map((x) => String(x.index)), String(state.act), actLabel);
  const act = currentAct();
  const variants = act?.variants || [];
  if (!variants.some((x) => x.id === state.actId)) state.actId = variants.find((x) => x.default)?.id || variants[0]?.id || state.actId;
  setupSelect($('#branchSelect'), variants.map((x) => x.id), state.actId, variantLabel);
  const branch = $('#branchSelect');
  if (branch) branch.disabled = variants.length <= 1;
  if ($('#seedInput')) $('#seedInput').value = state.seed;
  if ($('#modeSelect')) $('#modeSelect').value = state.mode;
  const editor = $('#contextEditor');
  const summary = $('#contextSummaryWrap');
  const editButton = $('#editStateButton');
  const editing = state.tab === 'build';
  if (editor) editor.hidden = !editing;
  if (summary) summary.hidden = editing;
  if (editButton) editButton.hidden = editing;
  if ($('#saveContext')) $('#saveContext').hidden = !(editing && state.mode === 'save');
  $('#playerStatEditor').hidden = !editing;
  $$('[data-player-stat]').forEach((input) => { input.value = state.playerStats[input.dataset.playerStat]; input.disabled = !canEditState(); });
  if ($('#savePathInput')) $('#savePathInput').value = state.save.path;
  renderSaveImport();
  renderStateSummary();
  if ($('#sideCharacter')) $('#sideCharacter').textContent = textFor(item('characters', state.character));
}

function imageMarkup(data, className = 'item-image', fallback = '✦') {
  const src = assetUrl(data);
  return src
    ? `<img class="${className}" src="${escapeHtml(src)}" alt="${escapeHtml(textFor(data))}" loading="lazy" onerror="this.hidden=true;this.nextElementSibling.hidden=false"><span class="image-fallback" hidden aria-hidden="true">${escapeHtml(fallback)}</span>`
    : `<span class="image-fallback ${className}" aria-hidden="true">${escapeHtml(fallback)}</span>`;
}

function maxUpgrade(data) { return Math.max(0, Number(data?.maxUpgradeLevel ?? 1)); }
function enchantments() { return Object.values(state.catalog?.enchantments || {}); }
function enchantmentLabel(id) { return id ? textFor(item('enchantments', id)) || textFor(state.catalog?.enchantments?.[id]) : '无附魔'; }
function costLabel(cost) {
  if (Number(cost) === -1) return state.language === 'eng' ? 'Unplayable' : '不能被打出';
  return `${Number(cost) === -2 ? 'X' : cost} ${state.language === 'eng' ? 'Energy' : '能量'}`;
}
function cardNumericLevel(entry) {
  return item('cards', entry.id).numericLevels?.[String(entry.upgrade || 0)] || null;
}
function cardGrowth(entry, props = entry.savedProps) {
  const schema = item('cards', entry.id).growth;
  if (!schema) return null;
  const current = props?.ints?.find((field) => field.name === schema.current);
  const increased = props?.ints?.find((field) => field.name === schema.increased);
  const unknown = !current && !increased && (entry.defaultedProps || []).some((field) => [schema.current, schema.increased].includes(field.name));
  const invalid = props === entry.savedProps && entry.growthDraft !== undefined;
  return { schema, unknown, invalid, value: invalid ? null : current?.value ?? (increased ? schema.base + increased.value : unknown ? null : schema.base) };
}
function setCardGrowth(entry, value) {
  const growth = cardGrowth(entry);
  if (!growth || !Number.isInteger(value) || value < growth.schema.base || value > growth.schema.max) throw new Error(state.language === 'eng' ? 'Enter a valid current card value.' : '请输入允许范围内的当前卡牌数值');
  setCardProperty(entry, growth.schema.current, value);
  setCardProperty(entry, growth.schema.increased, value - growth.schema.base);
  delete entry.growthDraft;
}
function validateCardGrowth(entry, props = entry.savedProps) {
  const growth = cardGrowth(entry, props);
  if (!growth) return;
  if (growth.invalid) throw new Error(`${textFor(item('cards', entry.id))}：请填写有效的${textFor(growth.schema)}`);
  if (growth.unknown) throw new Error(`${textFor(item('cards', entry.id))}：历史楼层没有记录成长值，请在卡面填写${textFor(growth.schema)}`);
  if (!Number.isInteger(growth.value) || growth.value < growth.schema.base || growth.value > growth.schema.max) throw new Error(`${textFor(item('cards', entry.id))}：${textFor(growth.schema)}须为 ${growth.schema.base}–${growth.schema.max} 的整数`);
  const increased = props?.ints?.find((field) => field.name === growth.schema.increased);
  if (increased && increased.value !== growth.value - growth.schema.base) throw new Error(`${textFor(item('cards', entry.id))}：当前数值与累计成长不一致，请在卡面重新设置`);
}
function cardNumericValues(entry) {
  const level = cardNumericLevel(entry), values = { ...(level?.vars || {}), ...(entry.dynamicVars || {}) };
  for (const key of level?.calculated || []) values[key] = null;
  const growth = cardGrowth(entry);
  if (growth) values[growth.schema.var] = growth.value;
  const rooms = entry.savedProps?.ints?.find((field) => field.name === 'RoomsEntered');
  if (entry.id === 'DOWSING' && rooms) values.Rooms = 5 - rooms.value;
  return values;
}
function splitCardExpression(text, separator) {
  let depth = 0, start = 0; const result = [];
  for (let i = 0; i < text.length; i += 1) {
    if ('{('.includes(text[i])) depth += 1;
    else if ('})'.includes(text[i])) depth -= 1;
    else if (text[i] === separator && depth === 0) { result.push(text.slice(start, i)); start = i + 1; }
  }
  result.push(text.slice(start)); return result;
}
function formatCardDescription(text, entry) {
  const values = { ...cardNumericValues(entry), IsUpgraded: Number(entry.upgrade) > 0, IfUpgraded: Number(entry.upgrade) > 0,
    IfNotUpgraded: Number(entry.upgrade) === 0, UpgradeLevel: Number(entry.upgrade), TargetType: cardNumericLevel(entry)?.targetType, singleStarIcon: '★' };
  const dynamic = state.language === 'eng' ? 'depends on combat' : '战斗中计算';
  const render = (source, nesting = 0) => {
    if (nesting > 8) return source;
    let result = '';
    for (let i = 0; i < source.length;) {
      if (source[i] !== '{') { result += source[i]; i += 1; continue; }
      let end = i + 1, depth = 1;
      while (end < source.length && depth) { if (source[end] === '{') depth += 1; if (source[end] === '}') depth -= 1; end += 1; }
      if (depth) { result += source.slice(i); break; }
      const parts = splitCardExpression(source.slice(i + 1, end - 1), ':'), name = parts[0], format = parts[1] || '';
      const value = values[name]; let replacement;
      if (format.startsWith('energyIcons(')) {
        if (name === 'energyPrefix') {
          const amount = Number(format.slice(12, -1)); replacement = amount === 1 ? '⚡' : `${amount}⚡`;
        } else replacement = `${value ?? dynamic} ${state.language === 'eng' ? 'Energy' : '能量'}`;
      } else if (format.startsWith('starIcons(')) replacement = `${value ?? dynamic} ★`;
      else if (format.startsWith('choose(')) {
        const choices = format.slice(7, -1).split('|'), options = splitCardExpression(parts.slice(2).join(':'), '|');
        replacement = options[choices.indexOf(String(value))] ?? (value == null ? options.join(' / ') : options[options.length - 1]);
      } else if (format === 'plural') {
        const options = splitCardExpression(parts.slice(2).join(':'), '|'); replacement = options[value === 1 ? 0 : options.length - 1];
      } else if (format === 'show') {
        const options = splitCardExpression(parts.slice(2).join(':'), '|'); replacement = options[value ? 0 : 1] || '';
      } else if (format === 'percentMore()' || format === 'percentLess()') {
        replacement = value == null ? dynamic : String(Math.round((format === 'percentMore()' ? value - 1 : 1 - value) * 100));
      } else if (parts.length > 1 && !['diff()', 'inverseDiff()'].includes(format) && !/^\d/.test(format)) {
        const options = splitCardExpression(parts.slice(1).join(':'), '|'); replacement = value == null ? options.filter(Boolean).join(' / ') : options[value ? 0 : 1] || '';
      } else replacement = value == null ? (cardGrowth(entry)?.schema.var === name ? (state.language === 'eng' ? 'set value' : '待指定') : dynamic) : String(value);
      result += render(String(replacement ?? ''), nesting + 1); i = end;
    }
    return result;
  };
  return render(text).replace(/\[\/?[^\]]+\]/g, '').trim();
}
function cardNumericMarkup(entry) {
  const values = cardNumericValues(entry), level = cardNumericLevel(entry), config = cardConfiguration(entry);
  if (!level) return '';
  const labels = { Damage: ['伤害', 'Damage'], Block: ['格挡', 'Block'], Cards: ['卡牌数', 'Cards'], Repeat: ['次数', 'Repeats'], Summon: ['召唤', 'Summon'], Forge: ['锻造', 'Forge'], OstyDamage: ['奥斯提伤害', 'Osty damage'] };
  const badges = Object.entries(labels).filter(([key]) => key in values && (!config || (key === 'Damage' && config.type?.type === 'ATTACK') || (key === 'Block' && config.type?.type === 'SKILL')))
    .map(([key, label]) => `<span class="card-stat ${key === 'Damage' ? 'damage' : key === 'Block' ? 'block' : ''}"><small>${label[state.language === 'eng' ? 1 : 0]}</small><b>${values[key] == null ? (state.language === 'eng' ? 'Variable' : '待指定') : escapeHtml(values[key])}</b></span>`);
  for (const [key, label] of [['CalculatedDamage', ['动态伤害', 'Variable damage']], ['CalculatedBlock', ['动态格挡', 'Variable block']]]) {
    if (key in values && values[key] == null) badges.push(`<span class="card-stat variable"><small>${label[state.language === 'eng' ? 1 : 0]}</small><b>${state.language === 'eng' ? 'In combat' : '战斗中计算'}</b></span>`);
  }
  return badges.length ? `<div class="card-numeric-stats">${badges.join('')}</div>` : '';
}
function cardGrowthFields(entry) {
  const growth = cardGrowth(entry); if (!growth) return '';
  const name = textFor(growth.schema), english = state.language === 'eng';
  return `<div class="card-growth-editor ${growth.unknown ? 'unknown' : ''}"><label for="growth-${entry.uid}">${escapeHtml(name)}</label><div class="card-growth-control"><input id="growth-${entry.uid}" type="number" step="1" min="${growth.schema.base}" max="${growth.schema.max}" required value="${escapeHtml(entry.growthDraft ?? growth.value ?? '')}" placeholder="${english ? 'Set value' : '待指定'}" aria-label="${escapeHtml(name)}" data-card-field="growthValue" data-uid="${entry.uid}" ${canEditState() ? '' : 'disabled'}><button type="button" class="mini-button" data-card-action="reset-growth" data-uid="${entry.uid}" ${canEditState() ? '' : 'disabled'}>${english ? 'Reset' : '重置成长'}</button></div><small data-growth-note>${escapeHtml(cardGrowthNote(entry))}</small></div>`;
}
function cardGrowthNote(entry) {
  const growth = cardGrowth(entry), english = state.language === 'eng';
  if (growth.invalid) return english ? 'Enter a whole number within the allowed range.' : '请输入允许范围内的整数。';
  if (growth.unknown) return english ? 'Not recorded at this floor. Set the current value.' : '历史楼层未记录成长值，请填写当前数值。';
  return `${english ? 'Accumulated growth' : '累计成长'} +${growth.value - growth.schema.base} · ${english ? 'Growth per play' : '每次使用成长'} +${cardNumericLevel(entry)?.vars.Increase ?? '—'}`;
}
function cardConfiguration(entry, props = entry.savedProps) {
  const schema = item('cards', entry.id).configuration;
  if (!schema) return null;
  const typeValue = propValue(props || {}, { group: 'ints', name: schema.typeProperty });
  const riderValue = propValue(props || {}, { group: 'ints', name: schema.riderProperty, default: 0 });
  return { schema, type: schema.types.find((type) => type.value === typeValue), typeValue, riderValue,
    rider: schema.riders.find((rider) => rider.value === riderValue) };
}
function validateCardConfiguration(entry, props = entry.savedProps) {
  const config = cardConfiguration(entry, props);
  if (!config) return;
  for (const property of [config.schema.typeProperty, config.schema.riderProperty]) {
    if ((props?.ints || []).filter((field) => field.name === property).length > 1) throw new Error(`${textFor(item('cards', entry.id))}：配置字段重复`);
  }
  if (!config.type) throw new Error(`${textFor(item('cards', entry.id))}：请指定攻击、技能或能力类型`);
  if (config.riderValue !== 0 && config.rider?.typeValue !== config.typeValue) throw new Error(`${textFor(item('cards', entry.id))}：附加效果与类型不匹配，请重新选择`);
}
function cardDisplayData(entry) {
  const data = item('cards', entry.id), config = cardConfiguration(entry);
  const level = cardNumericLevel(entry);
  const native = level ? { cost: level.cost, keywords: level.keywords.map((name) => state.catalog.ui.cardKeywords[name]).filter(Boolean) } : {};
  if (!config) return { ...data, ...native, description: formatCardDescription(data.description || '', entry), description_en: formatCardDescription(data.description_en || '', entry) };
  const effect = config.type?.effects?.[String(config.riderValue)];
  const unknown = state.language === 'eng' ? 'Choose card type and effect' : '请选择卡牌类型与附加效果';
  const keyword = Number(entry.upgrade) > 0 ? config.schema.upgradeKeyword : null;
  return { ...data, ...native, type: config.type?.type || 'NONE', ...(config.type ? { image: config.type.image } : {}),
    description: (effect?.description || unknown) + (keyword ? `\n${keyword.name}` : ''),
    description_en: (effect?.description_en || unknown) + (keyword ? `\n${keyword.name_en}` : '') };
}
function cardConfigurationFields(entry) {
  const config = cardConfiguration(entry);
  if (!config) return '';
  const disabled = !canEditState() ? 'disabled' : '';
  const types = config.schema.types.map((type) => `<option value="${type.value}" ${config.typeValue === type.value ? 'selected' : ''}>${escapeHtml(typeLabel(type.type))}</option>`).join('');
  const riders = config.schema.riders.filter((rider) => rider.typeValue === config.typeValue);
  const riderOptions = riders.map((rider) => `<option value="${rider.value}" ${config.riderValue === rider.value ? 'selected' : ''}>${escapeHtml(textFor(rider))}</option>`).join('');
  const placeholder = !config.type ? '<option value="" selected>请选择类型</option>' : '';
  const missingRider = !riders.some((rider) => rider.value === config.riderValue)
    ? `<option value="${config.riderValue === 0 ? '0' : ''}" selected>${config.riderValue === 0 ? '无附加效果' : '请重新选择'}</option>` : '';
  return `<div class="card-configuration-fields"><label class="card-field">${state.language === 'eng' ? 'Type' : '卡牌类型'}<select data-card-field="configurationType" data-uid="${entry.uid}" ${disabled}>${placeholder}${types}</select></label><label class="card-field">${state.language === 'eng' ? 'Effect' : '附加效果'}<select data-card-field="configurationRider" data-uid="${entry.uid}" ${disabled}>${missingRider}${riderOptions}</select></label></div>`;
}
function pickerCardData(data) {
  if (!data.configuration) return cardDisplayData({ id: data.id, upgrade: 0, savedProps: {}, defaultedProps: [] });
  const type = data.configuration.types.find((variant) => variant.type === state.filters.cardType) || data.configuration.types[0];
  const rider = data.configuration.riders.find((effect) => effect.typeValue === type.value);
  return { ...data, type: type.type, image: type.image, ...type.effects[String(rider.value)] };
}

function renderDeck() {
  const query = state.filters.deckQuery.trim().toLowerCase();
  const entries = state.deck.filter((entry) => {
    const data = cardDisplayData(entry);
    return !query || `${textFor(data)} ${data.name_en || ''} ${entry.id}`.toLowerCase().includes(query);
  });
  const total = state.deck.length;
  const kinds = new Set(state.deck.map((entry) => entry.id)).size;
  if ($('#deckCount')) $('#deckCount').textContent = `${formatCount(total, 'card')} · ${kinds} 种`;
  if ($('#sideDeckCount')) $('#sideDeckCount').textContent = formatCount(total, 'card');
  const add = document.querySelector('[data-picker="card"]'); if (add) add.hidden = !canEditState();
  const note = $('#deckReadOnlyState'); if (note) note.hidden = canEditState();
  if (state.mode === 'save' && !state.save.bundle) { $('#deckGrid').innerHTML = '<div class="empty-state">尚未读取存档</div>'; return; }
  if (!entries.length) {
    $('#deckGrid').innerHTML = '<div class="empty-state">尚未添加卡牌</div>';
    return;
  }
  $('#deckGrid').innerHTML = entries.map((entry) => {
    const data = cardDisplayData(entry);
    const max = Math.max(maxUpgrade(data), Number(entry.upgrade));
    const upgradeOptions = Array.from({ length: max + 1 }, (_, level) => `<option value="${level}" ${Number(entry.upgrade) === level ? 'selected' : ''}>${level ? `+${level}` : '未强化'}</option>`).join('');
    const enchantChoices = enchantments();
    if (entry.enchantmentId && !enchantChoices.some((e) => e.id === entry.enchantmentId)) enchantChoices.push(item('enchantments', entry.enchantmentId));
    const enchantOptions = [`<option value="" ${!entry.enchantmentId ? 'selected' : ''}>无附魔</option>`, ...enchantChoices.map((ench) => `<option value="${escapeHtml(ench.id)}" ${entry.enchantmentId === ench.id ? 'selected' : ''}>${escapeHtml(textFor(ench))}</option>`)].join('');
    const enchantAmount = entry.enchantmentId ? `<input class="card-amount-input" type="number" min="1" max="99" value="${escapeHtml(entry.enchantmentAmount)}" data-card-field="enchantmentAmount" data-uid="${entry.uid}" aria-label="附魔数值">` : '';
    return `<article class="deck-card card-instance">
      <div class="card-instance-header">
      ${imageMarkup(data, 'item-image')}
      <div class="item-content">
        <div class="item-title">${escapeHtml(textFor(data))}</div><div class="item-id">${escapeHtml(entry.id)}</div>
        <div class="item-meta"><span class="badge blue">${escapeHtml(typeLabel(data.type))}</span><span class="badge">${escapeHtml(rarityLabel(data.rarity))}</span>${data.cost != null && Number(data.cost) !== -1 ? `<span class="badge">${escapeHtml(costLabel(data.cost))}</span>` : ''}${data.multiplayerOnly ? '<span class="badge">多人专用</span>' : ''}</div>
      </div>
      </div>
        ${cardNumericMarkup(entry)}
        ${descriptionFor(data) ? `<p class="card-effect-description">${escapeHtml(descriptionFor(data))}</p>` : ''}
        ${data.keywords?.length ? `<div class="card-keywords">${data.keywords.map((keyword) => `<span>${escapeHtml(textFor(keyword))}</span>`).join('')}</div>` : ''}
        ${cardConfigurationFields(entry)}
        ${cardGrowthFields(entry)}
        <div class="card-state-fields">
          <label class="card-field">强化${maxUpgrade(data) === 0 ? '<span class="card-fixed-value">不可强化</span>' : `<select data-card-field="upgrade" data-uid="${entry.uid}" ${!canEditState() ? 'disabled' : ''}>${upgradeOptions}</select>`}</label>
          <label class="card-field">附魔<select data-card-field="enchantmentId" data-uid="${entry.uid}" ${!canEditState() ? 'disabled' : ''}>${enchantOptions}</select></label>
          ${enchantAmount ? `<label class="card-field amount-field">数值${enchantAmount}</label>` : ''}
        </div>
        ${entry.enchantmentId ? `<div class="card-instance-tags"><span class="badge gold">附魔：${escapeHtml(enchantmentLabel(entry.enchantmentId))}${entry.enchantmentAmount !== '' ? ` · ${escapeHtml(entry.enchantmentAmount)}` : ''}</span></div>` : ''}
        ${canEditState() ? `<div class="card-instance-actions">${entry.ref || Object.keys(entry.savedProps || {}).length ? `<button class="mini-button" data-card-action="props" data-uid="${entry.uid}">高级状态</button>` : ''}<button class="mini-button" data-card-action="duplicate" data-uid="${entry.uid}">复制此张</button><button class="mini-button remove-button" data-card-action="remove" data-uid="${entry.uid}">移除</button></div>` : ''}
    </article>`;
  }).join('');
}

function renderInventory(kind) {
  if (kind === 'relic') {
    const total = state.relics.length;
    $('#relicCount').textContent = formatCount(total, 'relic'); $('#sideRelicCount').textContent = formatCount(total, 'relic');
    document.querySelector('[data-picker="relic"]').hidden = !canEditState();
    const target = $('#relicGrid');
    if (state.mode === 'save' && !state.save.bundle) { target.innerHTML = '<div class="empty-state">尚未读取存档</div>'; return; }
    target.innerHTML = state.relics.map((entry, index) => {
      const data = item('relics', entry.id);
      const melted = propValue(entry.savedProps, { group: 'bools', name: 'IsMelted' });
      return `<article class="inventory-item relic-instance ${melted ? 'is-melted' : ''}" data-relic-uid="${entry.uid}">${imageMarkup(data)}<div class="item-content"><div class="item-title">${escapeHtml(textFor(data))}</div><div class="item-meta"><span class="badge">${escapeHtml(rarityLabel(data.rarity, 'relic'))}</span><span class="relic-position">#${index + 1}${entry.floorAdded != null ? ` · 第 ${entry.floorAdded} 层获得` : ''}</span></div><div class="description">${escapeHtml(plainDescription(data))}</div><div class="relic-state-summary">${relicStateSummary(entry)}</div>${canEditState() ? `<div class="relic-instance-actions"><button class="mini-button saved-props-button" data-inventory-action="props" data-kind="relic" data-id="${entry.uid}">编辑状态</button><button class="mini-button" data-inventory-action="inc" data-kind="relic" data-id="${entry.uid}">复制</button><button class="mini-button remove-button" data-inventory-action="remove" data-kind="relic" data-id="${entry.uid}">移除</button></div>` : ''}</div></article>`;
    }).join('') || '<div class="empty-state">尚未添加遗物</div>';
    return;
  }
  const counts = state[`${kind}s`];
  const entries = Object.entries(counts).filter(([, count]) => count > 0);
  const total = entries.reduce((sum, [, count]) => sum + count, 0);
  const label = kind === 'relic' ? 'relic' : 'potion';
  if ($(`#${kind}Count`)) $(`#${kind}Count`).textContent = formatCount(total, label);
  if ($(`#side${kind[0].toUpperCase() + kind.slice(1)}Count`)) $(`#side${kind[0].toUpperCase() + kind.slice(1)}Count`).textContent = formatCount(total, label);
  const add = document.querySelector(`[data-picker="${kind}"]`); if (add) add.hidden = !canEditState();
  const target = $(`#${kind}Grid`);
  if (state.mode === 'save' && !state.save.bundle) { target.innerHTML = '<div class="empty-state">尚未读取存档</div>'; return; }
  if (!entries.length) { target.innerHTML = `<div class="empty-state">${kind === 'relic' ? '尚未添加遗物' : '尚未添加药水'}</div>`; return; }
  target.innerHTML = entries.map(([id, count]) => {
    const data = item(`${kind}s`, id);
    return `<div class="inventory-item">${imageMarkup(data)}<div class="item-content"><div class="item-title">${escapeHtml(textFor(data))}</div><div class="item-meta"><span class="badge">${escapeHtml(rarityLabel(data.rarity, kind))}</span><span class="badge">×${count}</span></div><div class="description">${escapeHtml(plainDescription(data))}</div></div>${canEditState() ? `<div class="card-controls"><button class="mini-button" data-inventory-action="dec" data-kind="${kind}" data-id="${id}">−</button><button class="mini-button" data-inventory-action="inc" data-kind="${kind}" data-id="${id}">＋</button><button class="mini-button remove-button" data-inventory-action="remove" data-kind="${kind}" data-id="${id}">移除</button></div>` : ''}</div>`;
  }).join('');
}

function addItem(kind, id) {
  if (!canEditState()) return showToast('请先读取存档');
  if (kind === 'card') {
    const card = newCard(id), schema = item('cards', id).configuration;
    const type = schema?.types.find((choice) => choice.type === state.filters.cardType);
    if (type) {
      setCardProperty(card, schema.typeProperty, type.value);
      setCardProperty(card, schema.riderProperty, schema.riders.find((rider) => rider.typeValue === type.value).value);
    }
    state.deck.push(card);
  }
  else if (kind === 'relic') state.relics.push(newRelic(id));
  else {
    const key = `${kind}s`; const refs = (state.inventoryRefs[kind][id] ||= []);
    while (refs.length < (state[key][id] || 0)) refs.push({ id });
    refs.push({ id }); state[key][id] = (state[key][id] || 0) + 1;
  }
  markEdited(); renderAll();
}
function adjustInventory(kind, id, action) {
  if (!canEditState()) return;
  if (kind === 'relic') {
    const index = state.relics.findIndex((entry) => entry.uid === id); if (index < 0) return;
    const entry = state.relics[index];
    if (action === 'props') return openSavedProps(entry, textFor(item('relics', entry.id)), 'relic');
    if (action === 'inc') state.relics.splice(index + 1, 0, newRelic(entry.id, { ...entry, uid: undefined, ref: undefined, propsEdited: true }));
    else state.relics.splice(index, 1);
    markEdited(); renderAll(); return;
  }
  const key = `${kind}s`; if (!state[key][id]) return;
  const originals = state.inventoryRefs[kind][id] || [];
  if (action === 'inc') {
    while (originals.length < state[key][id]) originals.push({ id });
    originals.push({ id }); state.inventoryRefs[kind][id] = originals;
  }
  if (action === 'inc') state[key][id] += 1; else if (action === 'dec') state[key][id] -= 1; else delete state[key][id];
  if (action === 'dec') originals.pop();
  if (action === 'remove') delete state.inventoryRefs[kind][id];
  if (state[key][id] <= 0) delete state[key][id];
  markEdited(); renderAll();
}
function updateCard(uid, field, value) {
  const card = state.deck.find((entry) => entry.uid === uid); if (!card || !canEditState()) return;
  if (field === 'growthValue') {
    const input = document.getElementById(`growth-${uid}`);
    try {
      if (value === '') throw new Error(state.language === 'eng' ? 'Set the current value.' : '请填写当前卡牌数值');
      setCardGrowth(card, Number(value)); input?.setCustomValidity('');
    } catch (error) { card.growthDraft = value; input?.setCustomValidity(error.message); }
    // Update the preview without replacing the active input while typing.
    const container = input?.closest('.card-instance');
    if (container) {
      const data = cardDisplayData(card), stats = container.querySelector('.card-numeric-stats');
      if (stats) stats.outerHTML = cardNumericMarkup(card);
      container.querySelector('.card-effect-description').textContent = descriptionFor(data);
      container.querySelector('[data-growth-note]').textContent = cardGrowthNote(card);
      container.querySelector('.card-growth-editor').classList.toggle('unknown', cardGrowth(card).unknown || card.growthDraft !== undefined);
    }
    markEdited(); renderSaveImport(); return;
  }
  if (field === 'upgrade') card.upgrade = Number(value || 0);
  if (field === 'enchantmentId') { card.enchantmentId = value; if (!value) card.enchantmentAmount = ''; else if (card.enchantmentAmount === '') card.enchantmentAmount = 1; }
  if (field === 'enchantmentAmount') card.enchantmentAmount = value === '' ? '' : Math.max(1, Math.min(99, Number(value || 1)));
  if (field === 'configurationType' || field === 'configurationRider') {
    const config = cardConfiguration(card);
    if (!config || value === '') return;
    if (field === 'configurationType') {
      const type = config.schema.types.find((choice) => choice.value === Number(value)); if (!type) return;
      setCardProperty(card, config.schema.typeProperty, type.value);
      if (config.rider?.typeValue !== type.value) setCardProperty(card, config.schema.riderProperty, config.schema.riders.find((rider) => rider.typeValue === type.value).value);
    } else {
      const rider = config.schema.riders.find((choice) => choice.value === Number(value) && choice.typeValue === config.typeValue);
      if (!rider && Number(value) !== 0) return;
      setCardProperty(card, config.schema.riderProperty, Number(value));
    }
  }
  markEdited();
  if (field !== 'enchantmentAmount') renderDeck();
  renderStateSummary(); renderSaveImport();
}
function cardAction(uid, action) {
  const index = state.deck.findIndex((entry) => entry.uid === uid); if (index < 0 || !canEditState()) return;
  if (action === 'props') return openSavedProps(state.deck[index], textFor(item('cards', state.deck[index].id)));
  if (action === 'reset-growth') setCardGrowth(state.deck[index], cardGrowth(state.deck[index]).schema.base);
  if (action === 'remove') state.deck.splice(index, 1);
  if (action === 'duplicate') { const source = state.deck[index]; const copy = newCard(source.id, { ...source, uid: undefined }); state.deck.splice(index + 1, 0, copy); }
  markEdited(); renderAll();
}

function pickerFilters(kind) {
  const f = state.filters;
  if (kind === 'card') return `<label>搜索<input id="pickerQuery" value="${escapeHtml(f.cardQuery)}" placeholder="名称或 ID"></label><label>牌池<select id="pickerPool">${[['CURRENT','当前角色'],['ALL','全部角色'],['COLORLESS','无色'],['STATUS','状态'],['CURSE','诅咒'],['EVENT','事件/任务'],['OTHER','其他']].map(([v, t]) => `<option value="${v}" ${f.cardPool === v ? 'selected' : ''}>${state.language === 'eng' ? ({ CURRENT: 'Current character', ALL: 'All characters', COLORLESS: 'Colorless', STATUS: 'Status', CURSE: 'Curse', EVENT: 'Event / Quest', OTHER: 'Other' }[v]) : t}</option>`).join('')}</select></label><label>类型<select id="pickerType"><option value="ALL">全部类型</option>${['ATTACK','SKILL','POWER','STATUS','CURSE','QUEST'].map((v) => `<option value="${v}" ${f.cardType === v ? 'selected' : ''}>${escapeHtml(typeLabel(v))}</option>`).join('')}</select></label><label>稀有度<select id="pickerRarity"><option value="ALL">全部稀有度</option>${['BASIC','COMMON','UNCOMMON','RARE','ANCIENT'].map((v) => `<option value="${v}" ${f.cardRarity === v ? 'selected' : ''}>${escapeHtml(rarityLabel(v))}</option>`).join('')}</select></label><label>费用<select id="pickerCost"><option value="ALL">全部费用</option><option value="0" ${f.cardCost === '0' ? 'selected' : ''}>0</option><option value="1" ${f.cardCost === '1' ? 'selected' : ''}>1</option><option value="2" ${f.cardCost === '2' ? 'selected' : ''}>2</option><option value="3" ${f.cardCost === '3' ? 'selected' : ''}>3+</option></select></label>`;
  const values = kind === 'relic' ? ['STARTER','COMMON','UNCOMMON','RARE','ANCIENT','EVENT','SHOP'] : ['COMMON','UNCOMMON','RARE','EVENT','TOKEN'];
  const key = kind === 'relic' ? 'relic' : 'potion';
  return `<label>搜索<input id="pickerQuery" value="${escapeHtml(f[`${key}Query`])}" placeholder="名称或 ID"></label><label>稀有度<select id="pickerRarity"><option value="ALL">全部稀有度</option>${values.map((v) => `<option value="${v}" ${f[`${key}Rarity`] === v ? 'selected' : ''}>${escapeHtml(rarityLabel(v, kind))}</option>`).join('')}</select></label><div class="filter-spacer"></div>`;
}
function openPicker(kind) {
  if (!canEditState()) return showToast('请先读取存档');
  state.picker.kind = kind;
  const titles = { card: ['CARD LIBRARY', '选择卡牌', '游戏内正式文本'], relic: ['RELIC LIBRARY', '选择遗物', '游戏内正式文本'], potion: ['POTION LIBRARY', '选择药水', '游戏内正式文本'] }[kind];
  $('#modalKicker').textContent = titles[0]; $('#modalTitle').textContent = titles[1]; $('#modalSubtitle').textContent = titles[2]; $('#pickerFilters').innerHTML = pickerFilters(kind); $('#modalBackdrop').hidden = false;
  bindPickerFilters(); renderPicker();
}
function closePicker() { $('#modalBackdrop').hidden = true; state.picker.kind = null; }
function bindPickerFilters() {
  const kind = state.picker.kind; if (!kind) return;
  const key = kind === 'card' ? 'card' : kind;
  const q = $('#pickerQuery'); q?.addEventListener('input', () => { state.filters[`${key}Query`] = q.value; renderPicker(); });
  const rarity = $('#pickerRarity'); rarity?.addEventListener('change', () => { state.filters[`${key}Rarity`] = rarity.value; renderPicker(); });
  if (kind === 'card') for (const [id, prop] of [['pickerPool', 'cardPool'], ['pickerType', 'cardType'], ['pickerCost', 'cardCost']]) $(`#${id}`)?.addEventListener('change', (event) => { state.filters[prop] = event.target.value; renderPicker(); });
}
function pickerMatches(kind, data) {
  const f = state.filters; const key = kind === 'card' ? 'card' : kind; const query = f[`${key}Query`].trim().toLowerCase();
  if (query && !`${data.id} ${data.name || ''} ${data.name_en || ''} ${plainDescription(data)}`.toLowerCase().includes(query)) return false;
  if (kind !== 'card') return f[`${key}Rarity`] === 'ALL' || data.rarity === f[`${key}Rarity`];
  if (f.cardRarity !== 'ALL' && data.rarity !== f.cardRarity) return false;
  if (f.cardType !== 'ALL' && data.type !== f.cardType && !data.configuration?.types.some((type) => type.type === f.cardType)) return false;
  if (f.cardCost !== 'ALL') { const cost = Number(data.cost); if (f.cardCost === '3' ? !(cost >= 3) : cost !== Number(f.cardCost)) return false; }
  const pools = new Set(data.pools || []);
  if (f.cardPool === 'CURRENT' && !pools.has(state.character)) return false;
  if (f.cardPool === 'COLORLESS' && !pools.has('COLORLESS')) return false;
  if (f.cardPool === 'STATUS' && !pools.has('STATUS') && data.type !== 'STATUS') return false;
  if (f.cardPool === 'CURSE' && !pools.has('CURSE') && data.type !== 'CURSE') return false;
  if (f.cardPool === 'EVENT' && !pools.has('EVENT') && !pools.has('QUEST')) return false;
  if (f.cardPool === 'OTHER' && [...pools].some((x) => ['IRONCLAD','SILENT','DEFECT','NECROBINDER','REGENT','COLORLESS','STATUS','CURSE','EVENT','QUEST'].includes(x))) return false;
  return true;
}
function renderPicker() {
  const kind = state.picker.kind; if (!kind) return;
  const values = allItems(`${kind}s`).filter((data) => pickerMatches(kind, data)).sort((a, b) => textFor(a).localeCompare(textFor(b), state.language === 'eng' ? 'en' : 'zh'));
  $('#pickerCount').textContent = `${values.length} 个结果`;
  $('#pickerGrid').innerHTML = values.map((raw) => {
    const data = kind === 'card' ? pickerCardData(raw) : raw;
    return `<article class="picker-item">${imageMarkup(data)}<div class="item-content"><div class="item-title">${escapeHtml(textFor(data))}</div><div class="item-id">${escapeHtml(data.id)}</div><div class="item-meta"><span class="badge">${escapeHtml(rarityLabel(data.rarity, kind))}</span>${kind === 'card' ? `<span class="badge blue">${escapeHtml(typeLabel(data.type))}</span>${data.cost != null && Number(data.cost) !== -1 ? `<span class="badge">${escapeHtml(costLabel(data.cost))}</span>` : ''}${data.multiplayerOnly ? '<span class="badge">多人专用</span>' : ''}` : ''}</div><div class="description">${escapeHtml(plainDescription(data))}</div><button class="add-item" data-add-kind="${kind}" data-id="${data.id}">＋ 添加一${{ card: '张', relic: '件', potion: '瓶' }[kind]}</button></div></article>`;
  }).join('') || '<div class="empty-state">没有匹配的条目，请调整筛选条件。</div>';
}

function groupFor(stage, branch, tier) {
  const groups = state.catalog?.encounterGroups || [];
  const kind = tier === 'weak' || tier === 'strong' ? tier : tier;
  const candidates = groups.filter((group) => String(group.kind).toLowerCase() === kind && Number(group.actIndex) === Number(stage) && (!group.actIds || !group.actIds.length || group.actIds.includes(branch)));
  return candidates.find((group) => (group.actIds || []).length === 1 && group.actIds[0] === branch) || candidates.find((group) => (group.actIds || []).length > 1) || candidates[0] || null;
}
function allCurrentActGroup(stage, branch) {
  const groups = state.catalog?.encounterGroups || [];
  return groups.find((group) => String(group.kind).toLowerCase() === 'all' && Number(group.actIndex) === Number(stage) && (group.actIds || []).includes(branch)) || groups.find((group) => String(group.kind).toLowerCase() === 'all' && Number(group.actIndex) === Number(stage));
}
function encounterTierLabel(tier) { return ({ weak: '弱怪池', strong: '强怪池', elite: '精英池', boss: 'Boss 池' })[tier] || tier; }
function poolPresetLabel(preset) { return ({ CURRENT_WEAK: '当前分支 · 弱怪池', CURRENT_STRONG: '当前分支 · 强怪池', CURRENT_ELITE: '当前分支 · 精英池', CURRENT_BOSS: '当前分支 · Boss 池', CUSTOM: '自选池' })[preset] || preset; }
function selectedEncounterGroup() { return groupFor(state.encounterFilters.stage, state.encounterFilters.branch, state.encounterFilters.groupTier); }
function encounterScope() {
  const f = state.encounterFilters;
  if (f.mode === 'group') return selectedEncounterGroup()?.encounters || [];
  if (f.poolPreset && f.poolPreset !== 'CUSTOM') return groupFor(f.stage, f.branch, f.poolPreset.replace('CURRENT_', '').toLowerCase())?.encounters || [];
  return allCurrentActGroup(f.stage, f.branch)?.encounters || Object.keys(state.catalog.encounters || {});
}
function renderEncounterFilters() {
  const f = state.encounterFilters;
  setupSelect($('#encounterStage'), (state.catalog.acts || []).map((x) => String(x.index)), String(f.stage), actLabel);
  const act = (state.catalog.acts || []).find((x) => String(x.index) === String(f.stage)) || state.catalog.acts?.[0];
  const variants = act?.variants || [];
  if (!variants.some((x) => x.id === f.branch)) f.branch = variants.find((x) => x.default)?.id || variants[0]?.id || f.branch;
  setupSelect($('#encounterBranch'), variants.map((x) => x.id), f.branch, variantLabel);
  const branchSelect = $('#encounterBranch'); if (branchSelect) branchSelect.disabled = variants.length <= 1;
  setupSelect($('#encounterMode'), ['group', 'pool'], f.mode, (value) => value === 'group' ? '当前阶段分支的怪物组' : '直接选择随机池');
  setupSelect($('#encounterGroupTier'), ['weak', 'strong', 'elite', 'boss'], f.groupTier, encounterTierLabel);
  setupSelect($('#encounterPoolPreset'), ['CURRENT_WEAK', 'CURRENT_STRONG', 'CURRENT_ELITE', 'CURRENT_BOSS', 'CUSTOM'], f.poolPreset, poolPresetLabel);
  const groupField = $('#groupTierField'); if (groupField) groupField.hidden = f.mode !== 'group';
  const poolField = $('#poolPresetField'); if (poolField) poolField.hidden = f.mode !== 'pool';
  if ($('#encounterSearch')) $('#encounterSearch').value = f.search;
  renderEncounterSource();
}
function renderEncounterSource() {
  const f = state.encounterFilters;
  const group = f.mode === 'group' ? selectedEncounterGroup() : (f.poolPreset === 'CUSTOM' ? null : groupFor(f.stage, f.branch, f.poolPreset.replace('CURRENT_', '').toLowerCase()));
  const ids = group?.encounters || encounterScope();
  const title = f.mode === 'group' ? `${actLabel(f.stage)} · ${variantLabel((state.catalog.acts || []).flatMap((a) => a.variants || []).find((v) => v.id === f.branch))} · ${encounterTierLabel(f.groupTier)}` : poolPresetLabel(f.poolPreset);
  if ($('#encounterSourceTitle')) $('#encounterSourceTitle').textContent = title;
  if ($('#encounterSourceDescription')) $('#encounterSourceDescription').textContent = group ? `${textFor(group)} · ${ids.length} 个遭遇` : `在当前阶段与分支范围内自选遭遇 · ${ids.length} 个可选`;
  if ($('#customPoolNote')) $('#customPoolNote').hidden = !(f.mode === 'pool' && f.poolPreset === 'CUSTOM');
  $$('#quickPoolButtons [data-pool-preset]').forEach((button) => button.classList.toggle('active', f.mode === 'pool' && f.poolPreset === button.dataset.poolPreset));
}
function encounterMatches(id, data) {
  const f = state.encounterFilters; const query = f.search.trim().toLowerCase(); const scope = new Set(encounterScope());
  if (!scope.has(id)) return false;
  const names = (data.monsters || []).concat(data.monsters_en || []);
  return !query || `${id} ${textFor(data)} ${names.join(' ')}`.toLowerCase().includes(query);
}
function encounterPoolTag(data) { return data.kind === 'normal' ? (data.difficulty === 'weak' ? '弱怪' : '强怪') : data.kind === 'elite' ? '精英' : data.kind === 'boss' ? 'Boss' : data.kind; }
function renderEncounters() {
  const rows = Object.entries(state.catalog.encounters || {}).filter(([id, data]) => encounterMatches(id, data)).sort(([, a], [, b]) => textFor(a).localeCompare(textFor(b), state.language === 'eng' ? 'en' : 'zh'));
  $('#encounterStatus').textContent = `当前范围 ${rows.length} 个`;
  $('#encounterGrid').innerHTML = rows.map(([id, data]) => { const selected = state.selectedEncounters.has(id); const monsters = (state.language === 'eng' ? data.monsters_en : data.monsters) || data.monsters_en || []; return `<article class="encounter-card ${selected ? 'selected' : ''}" data-encounter-id="${escapeHtml(id)}">${imageMarkup(data, 'item-image monster', '⚔')}<div class="item-content"><div class="item-title">${escapeHtml(textFor(data))}</div><div class="item-meta"><span class="badge blue">${escapeHtml(encounterPoolTag(data))}</span><span class="badge">${actLabel(data.actIndex)}</span></div><div class="description">${escapeHtml(monsters.join('、'))}</div><div class="item-id">${escapeHtml(id)}</div></div></article>`; }).join('') || '<div class="empty-state">当前阶段、分支和池子中没有匹配项，请调整搜索。</div>';
  $('#encounterSelectionText').textContent = `已选 ${state.selectedEncounters.size} 个遭遇`;
}
function renderEncounterLists() {
  const tagList = (ids, target, countTarget) => { $(countTarget).textContent = ids.length; $(target).innerHTML = ids.length ? ids.map((id) => `<span class="tag">${escapeHtml(textFor(item('encounters', id)) || id)}<button data-remove-encounter="${escapeHtml(id)}" data-list="${target.includes('pool') ? 'pool' : 'direct'}" aria-label="移除">×</button></span>`).join('') : '<span class="empty-copy">尚未添加</span>'; };
  tagList(state.direct, '#directList', '#directCount'); tagList(state.pool, '#poolList', '#poolCount');
  $('#sideEncounterCount').textContent = formatCount(state.direct.length || state.pool.length, 'encounter');
  if ($('#encounterSourceMode')) $('#encounterSourceMode').textContent = state.pool.length ? '随机池' : '指定遭遇';
}
function applyEncounterSource() {
  const ids = encounterScope();
  if (!ids.length) return showToast('当前阶段、分支和池子没有可用遭遇');
  state.pool = Array.from(new Set(ids)); state.direct = []; state.encounterSource = 'pool'; state.selectedEncounters.clear(); renderAll(); showToast(`已使用 ${ids.length} 个遭遇作为随机池`);
}

function setSettingOptions(id, options, value) {
  const select = $(`#${id}`);
  if (!select) return;
  select.innerHTML = options.map(([optionValue, label]) => `<option value="${escapeHtml(optionValue)}">${escapeHtml(label)}</option>`).join('');
  select.value = value;
  if (select.value !== value && options.length) select.value = options[0][0];
}
function renderRuntimeEstimate() {
  const target = $('#runtimeEstimate');
  if (!target || !state.catalog) return;
  const samples = Math.max(1, Number(state.settings.samples || 1));
  const selected = state.direct.length ? state.direct : (state.pool.length ? state.pool : encounterScope());
  const cases = state.pool.length ? samples : Math.max(1, selected.length) * samples;
  const dop = Math.max(1, Number(state.settings.dop || 1));
  const workers = Math.max(1, Number(state.settings.caseWorkers || 1));
  const english = state.language === 'eng';
  target.textContent = english
    ? `Workload: ${cases} case${cases === 1 ? '' : 's'} (${samples} sample${samples === 1 ? '' : 's'}). Per-case DOP ${dop}, up to ${workers} isolated case process${workers === 1 ? '' : 'es'}; sample count is approximately linear in runtime.`
    : `当前工作量：${cases} 场（${samples} 个样本）。单场 DOP ${dop}，最多 ${workers} 个独立场次进程；样本数增加后，耗时大致线性增加。`;
}
function renderSettings() {
  const s = state.settings;
  s.language = state.language === 'eng' ? 'eng' : 'zhs';
  const english = state.language === 'eng';
  const settingsEyebrow = document.querySelector('#tab-settings .eyebrow'); if (settingsEyebrow) settingsEyebrow.textContent = english ? 'SOLVER CONTROL' : '求解器控制';
  const profileKicker = document.querySelector('#tab-settings .settings-main .panel-kicker'); if (profileKicker) profileKicker.textContent = english ? 'SEARCH PROFILE' : '搜索配置';
  const overridesKicker = document.querySelector('#tab-settings .settings-side .panel-kicker'); if (overridesKicker) overridesKicker.textContent = english ? 'OPTIONAL OVERRIDES' : '高级覆盖项';
  const budgetInput = $('#budgetInput'); const budgetLabel = budgetInput?.closest('label'); if (budgetLabel) budgetLabel.firstChild.textContent = english ? 'Time limit (seconds)' : '时间上限（秒）'; if (budgetInput) { budgetInput.min = '0.1'; budgetInput.step = '0.1'; }
  const settingLabels = {
    profileSelect: ['性能预设', 'Performance preset'], searchModeSelect: ['搜索模式', 'Search mode'], potionPolicySelect: ['药水政策', 'Potion policy'], languageSelect: ['求解语言', 'Solver language'], samplesInput: ['样本数', 'Samples'], poolSeedInput: ['池种子', 'Pool seed'], dopInput: ['单场并行度 DOP', 'Per-case parallelism (DOP)'], caseWorkersInput: ['场次并行', 'Parallel cases'], beamInput: ['Beam 宽度', 'Beam width'], nodesInput: ['最大节点', 'Max nodes'], cardBranchesInput: ['卡牌分支', 'Card branches per node'], pileBranchesInput: ['牌堆分支', 'Pile choice branches'], handBranchesInput: ['手牌分支', 'Hand choice branches'],
  };
  for (const [id, labels] of Object.entries(settingLabels)) { const field = $(`#${id}`); const label = field?.closest('label'); if (label?.firstChild) label.firstChild.textContent = english ? labels[1] : labels[0]; }
  const switchLabels = { stopZeroInput: ['达到战损目标后停止搜索', 'Stop when HP-loss target is met'], productionInput: ['使用生产预算流程', 'Use production budget flow'], measureInput: ['记录阶段耗时', 'Measure phase timings'] };
  for (const [id, labels] of Object.entries(switchLabels)) { const span = $(`#${id}`)?.parentElement?.querySelector('span'); if (span) span.textContent = english ? labels[1] : labels[0]; }
  const budgetHeading = document.querySelector('#tab-settings .settings-main h3'); if (budgetHeading) budgetHeading.textContent = english ? 'Search budget' : '搜索预算';
  const overrideHeading = document.querySelector('#tab-settings .settings-side h3'); if (overrideHeading) overrideHeading.textContent = english ? 'Advanced overrides' : '高级覆盖项';
  const infoBadge = document.querySelector('#tab-settings .info-badge'); if (infoBadge) infoBadge.textContent = english ? 'Higher is slower but searches more thoroughly' : '越高越慢，但搜索更充分';
  setSettingOptions('profileSelect', english
    ? [['Low', 'Low (60 s)'], ['Medium', 'Medium (default, 120 s)'], ['High', 'High (180 s)'], ['VeryHigh', 'Very high (300 s)'], ['Custom', 'Custom']]
    : [['Low', '低档（60 秒）'], ['Medium', '中档（默认，120 秒）'], ['High', '高档（180 秒）'], ['VeryHigh', '极高（300 秒）'], ['Custom', '自定义']], s.profile);
  setSettingOptions('searchModeSelect', english
    ? [['Evaluate', 'Single search'], ['Coordinator', 'Production coordinator']]
    : [['Evaluate', '单次求解'], ['Coordinator', '生产协调器']], s.searchMode);
  setSettingOptions('potionPolicySelect', english
    ? [['Smart', 'Smart'], ['RequireAtLeastOne', 'Keep at least one'], ['Disabled', 'Disable potions']]
    : [['Smart', '智能'], ['RequireAtLeastOne', '至少保留一瓶'], ['Disabled', '禁用药水']], s.potionPolicy);
  setSettingOptions('languageSelect', [['zhs', '简体中文'], ['eng', english ? 'English' : '英文']], s.language);
  for (const [id, key] of [['languageSelect', 'language'], ['samplesInput', 'samples'], ['poolSeedInput', 'poolSeed'], ['dopInput', 'dop'], ['caseWorkersInput', 'caseWorkers'], ['beamInput', 'beam'], ['nodesInput', 'nodes'], ['cardBranchesInput', 'cardBranches'], ['pileBranchesInput', 'pileBranches'], ['handBranchesInput', 'handBranches'], ['stopZeroInput', 'stopAtZeroLoss'], ['productionInput', 'productionBudget'], ['measureInput', 'measurePhases']]) { const el = $(`#${id}`); if (!el) continue; if (el.type === 'checkbox') el.checked = Boolean(s[key]); else el.value = s[key] ?? ''; }
  if (budgetInput) budgetInput.value = (Number(s.budgetMs || 0) / 1000).toString();
  renderRuntimeEstimate();
}
function renderAll() { renderContext(); renderDeck(); renderInventory('relic'); renderInventory('potion'); renderEncounterFilters(); renderEncounters(); renderEncounterLists(); renderSettings(); const languageToggle = $('#languageToggle'); if (languageToggle) languageToggle.textContent = state.language === 'eng' ? 'English' : '简体中文'; setTab(state.tab); }
function collectSettings() {
  const s = state.settings;
  for (const [id, key] of [['languageSelect', 'language'], ['profileSelect', 'profile'], ['searchModeSelect', 'searchMode'], ['potionPolicySelect', 'potionPolicy'], ['samplesInput', 'samples'], ['poolSeedInput', 'poolSeed'], ['dopInput', 'dop'], ['caseWorkersInput', 'caseWorkers'], ['beamInput', 'beam'], ['nodesInput', 'nodes'], ['cardBranchesInput', 'cardBranches'], ['pileBranchesInput', 'pileBranches'], ['handBranchesInput', 'handBranches'], ['stopZeroInput', 'stopAtZeroLoss'], ['productionInput', 'productionBudget'], ['measureInput', 'measurePhases']]) { const el = $(`#${id}`); if (!el) continue; s[key] = el.type === 'checkbox' ? el.checked : (['samples', 'dop', 'caseWorkers'].includes(key) ? Number(el.value || 0) : el.value); }
  const budgetInput = $('#budgetInput'); if (budgetInput) s.budgetMs = Math.round(Number(budgetInput.value || 0) * 1000);
  if (s.language === 'eng' || s.language === 'zhs') state.language = s.language;
  return s;
}
function buildPlan() {
  const invalidCardInput = $$('#deckGrid input[data-card-field]').find((input) => !input.checkValidity());
  if (invalidCardInput) { invalidCardInput.reportValidity(); throw new Error('请先修正卡牌上未填写或超出范围的数值'); }
  const s = collectSettings(); const search = { profile: s.profile, searchMode: s.searchMode, potionPolicy: s.potionPolicy, language: s.language, samples: s.samples, poolSeed: s.poolSeed, budgetMs: s.budgetMs, dop: s.dop, caseWorkers: s.caseWorkers };
  for (const key of ['beam', 'nodes', 'cardBranches', 'pileBranches', 'handBranches']) if (s[key] !== '') search[key] = Number(s[key]);
  for (const key of ['stopAtZeroLoss', 'productionBudget', 'measurePhases']) if (s[key]) search[key] = true;
  const selected = state.direct.length ? state.direct : (state.pool.length ? state.pool : encounterScope());
  if (!selected.length) throw new Error('请先选择至少一个测试遭遇');
  if (state.save.busy) throw new Error('存档仍在读取中');
  if (state.mode === 'save' && !state.save.bundle) throw new Error('请先读取存档');
  if (!state.deck.length) throw new Error('请至少保留一张卡牌');
  for (const entry of state.deck) { validateCardConfiguration(entry); validateCardGrowth(entry); }
  if (state.playerStats.hp < 1 || state.playerStats.hp > state.playerStats.maxHp) throw new Error('评估前生命必须在 1 到最大生命之间');
  if (Object.values(state.potions).reduce((a, b) => a + b, 0) > state.playerStats.potionSlots) throw new Error('药水数量超过槽数，请先调整');
  const deck = state.deck.map(({ id, upgrade, enchantmentId, enchantmentAmount, ref, savedProps, propsEdited, dynamicVars }) => {
    const card = { id, count: 1, upgrade };
    if (enchantmentId) {
      card.enchantmentId = enchantmentId;
      if (enchantmentAmount !== '' && enchantmentAmount != null) card.enchantmentAmount = Number(enchantmentAmount);
    }
    if (ref) card.ref = ref;
    if (propsEdited || item('cards', id).configuration) card.savedProps = savedProps;
    if (dynamicVars && Object.keys(dynamicVars).length) card.dynamicVars = structuredClone(dynamicVars);
    return card;
  });
  const plan = { mode: 'build', inputMode: state.mode, seed: state.seed, ascension: state.ascension, encounters: state.direct.length ? state.direct : (state.pool.length ? [] : selected), samples: s.samples, poolSeed: s.poolSeed, search };
  Object.assign(plan, {
    character: state.character, act: state.act, actId: state.actId,
    deck,
    relics: inventoryPlan('relic'), potions: inventoryPlan('potion'), playerStats: { ...state.playerStats },
    clearStartingRelics: true,
  });
  if (state.save.bundle) {
    plan.saveImport = { sourceId: state.save.bundle.source.id, selection: { ...state.save.bundle.selection } };
    plan.importEdited = state.save.dirty;
  }
  if (state.pool.length) plan.pool = state.pool.slice();
  if (!state.direct.length && !state.pool.length) plan.pool = selected.slice();
  return plan;
}

function formatElapsed(milliseconds) {
  const seconds = Math.max(0, Number(milliseconds || 0)) / 1000;
  if (seconds < 60) return `${seconds.toFixed(1)} 秒`;
  return `${Math.floor(seconds / 60)} 分 ${Math.floor(seconds % 60)} 秒`;
}
function updateJobView(job) {
  if (!job) return;
  const report = job.report;
  const progress = job.progress || {};
  const stateLabel = ({ queued: '排队中', running: '正在评估', completed: '评估完成', failed: '评估失败', stopped: '已停止', stopping: '正在停止' }[job.status] || job.status);
  const total = Number(progress.total || 0);
  const completed = Number(progress.completed || 0);
  const current = Number(progress.current || 0);
  const percent = Math.max(0, Math.min(100, Number(progress.percent ?? (total ? completed / total * 100 : 0))));
  const encounter = progress.label || progress.encounter || '';
  const caseText = total ? `第 ${current || completed || 1}/${total} 场${encounter ? ` · ${encounter}` : ''}` : '等待场次信息';
  const nodeText = progress.expanded == null ? '' : ` · 已展开 ${Number(progress.expanded).toLocaleString()} 节点`;
  $('#jobState').textContent = stateLabel;
  $('#jobDetail').textContent = job.error || `${caseText} · 已用 ${formatElapsed(progress.elapsedMs)}${nodeText}`;
  if ($('#progressPhase')) $('#progressPhase').textContent = progress.phase || '等待任务';
  const workerText = Number(progress.caseWorkers || 1) > 1 ? ` · 场次并行 ${Number(progress.caseWorkers)}` : '';
  const dopText = Number(progress.dop || 1) > 1 ? ` · 单场 DOP ${Number(progress.dop)}` : '';
  if ($('#progressStats')) $('#progressStats').textContent = `${total ? `已完成 ${completed}/${total} 场` : '尚未开始'} · ${percent.toFixed(0)}% · ${formatElapsed(progress.elapsedMs)}${workerText}${dopText}`;
  $('#jobLog').textContent = (job.lines || []).join('\n') || '等待本地评估任务…';
  $('#jobLog').scrollTop = $('#jobLog').scrollHeight;
  $('#stopButton').disabled = !['queued', 'running', 'stopping'].includes(job.status);
  $('#runButton').disabled = ['queued', 'running', 'stopping'].includes(job.status);
  const bar = $('#progressBar');
  bar.style.width = `${percent}%`;
  bar.classList.toggle('indeterminate', Boolean(progress.indeterminate && job.status === 'running'));
  if (report) renderReport(report);
  if (['completed', 'failed', 'stopped'].includes(job.status)) { clearInterval(state.pollTimer); state.pollTimer = null; $('#resultDot').hidden = false; }
}
function renderReport(report) {
  state.report = report; const summary = report.summary || {}; const stats = (key) => summary[key]?.mean == null ? '—' : Number(summary[key].mean).toFixed(1); $('#metricCompletion').textContent = summary.completionRate == null ? '—' : `${Math.round(summary.completionRate * 100)}%`; $('#metricLoss').textContent = stats('projectedHpLoss'); $('#metricHp').textContent = stats('finalHp'); $('#metricPotions').textContent = stats('potionUses'); $('#metricTurns').textContent = stats('combatEndedTurn'); $('#resultSubtitle').textContent = summary.interpretation || '同一搜索预算和遭遇样本下的评估结果。';
  $('#resultRows').innerHTML = (report.cases || []).map((row) => { const m = row.metrics || {}; const pass = String(m.status || '').toLowerCase() === 'passed'; return `<tr><td>${escapeHtml(textFor(item('encounters', row.encounter)) || row.encounter)}</td><td>${row.sample || 1}</td><td class="${pass ? 'status-pass' : 'status-fail'}">${pass ? '完成' : escapeHtml(m.status || '失败')}</td><td>${m.projectedHpLoss ?? '—'}</td><td>${m.finalHp ?? '—'}</td><td>${m.potionUses ?? '—'}</td><td>${m.combatEndedTurn ?? '—'}</td></tr>`; }).join('');
}
async function startEvaluation() { try { const plan = buildPlan(); setTab('results'); $('#jobState').textContent = '正在提交任务'; const response = await fetch('/api/evaluate', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(plan) }); const data = await response.json(); if (!response.ok) throw new Error(data.error || '无法建立评估任务'); state.jobId = data.id; updateJobView(data); state.pollTimer = setInterval(async () => { const result = await fetch(`/api/jobs/${state.jobId}`).then((r) => r.json()); updateJobView(result); }, 850); } catch (error) { showToast(error.message, 5000); } }
async function savePlan() { try { const plan = buildPlan(); const response = await fetch('/api/plan', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(plan) }); if (!response.ok) throw new Error((await response.json()).error || '保存失败'); showToast('方案已保存到本地服务'); } catch (error) { showToast(error.message, 5000); } }
async function loadPlan() { try { const response = await fetch('/api/plan'); if (!response.ok) throw new Error('本地服务中尚未保存方案'); const plan = await response.json(); await applyPlan(plan); showToast('已载入本地方案'); } catch (error) { showToast(error.message, 4000); } }
async function applyPlan(plan) {
  let bundle = null;
  if (plan.saveImport) bundle = await saveApi('/api/saves/state', { sourceId: plan.saveImport.sourceId, selection: plan.saveImport.selection });
  else if ((plan.mode === 'save' || plan.mode === 'snapshot') && plan.savePath) bundle = await saveApi('/api/saves/import', { path: plan.savePath });
  state.save.bundle = null; state.save.drafts.clear(); state.save.dirty = false; state.save.error = '';
  state.inventoryRefs = { relic: {}, potion: {} };
  if (bundle) applyImportedState(bundle, { resetDrafts: true });
  state.mode = plan.inputMode || (bundle ? 'save' : 'build'); state.character = plan.character || bundle?.character || state.character;
  state.seed = plan.seed || bundle?.seed || state.seed; state.ascension = Number(plan.ascension ?? bundle?.ascension ?? 10);
  state.act = Number(plan.act ?? plan.actIndex ?? bundle?.act ?? 0); state.actId = plan.actId || bundle?.actId || state.actId;
  state.save.path = bundle?.source.path || ''; state.save.dirty = !!plan.importEdited;
  const startingHp = { IRONCLAD: 80, SILENT: 70, DEFECT: 75, NECROBINDER: 66, REGENT: 75 }[state.character] || 80;
  state.playerStats = { ...(bundle?.stats || { hp: startingHp, maxHp: startingHp, gold: 99, maxEnergy: 3, potionSlots: state.ascension >= 4 ? 2 : 3, orbSlots: state.character === 'DEFECT' ? 3 : 0 }), ...(plan.playerStats || {}) };
  const originalCards = new Map((bundle?.deck || []).map((c) => [c.ref, c]));
  if (plan.deck) {
    state.deck = []; plan.deck.forEach((entry) => {
      const value = typeof entry === 'string' ? { id: entry } : entry;
      const original = originalCards.get(value.ref) || {};
      for (let i = 0; i < Number(value.count || 1); i += 1) state.deck.push(newCard(value.id || value.cardId, { ...original, ...value, propsEdited: 'savedProps' in value }));
    });
  }
  if (plan.relics) {
    const originals = new Map((bundle?.relics || []).map((v) => [v.ref, v]));
    state.relics = plan.relics.flatMap((entry) => {
      const value = typeof entry === 'string' ? { id: entry } : entry;
      return Array.from({ length: Number(value.count || 1) }, () => newRelic(value.id || value.relicId, { ...originals.get(value.ref), ...value, propsEdited: 'savedProps' in value }));
    });
  }
  for (const kind of ['potion']) if (plan[`${kind}s`]) {
    const originals = new Map((bundle?.[`${kind}s`] || []).map((v) => [v.ref, v]));
    state[`${kind}s`] = {}; state.inventoryRefs[kind] = {};
    for (const entry of plan[`${kind}s`]) {
      const value = typeof entry === 'string' ? { id: entry } : entry;
      const id = value.id || value[`${kind}Id`]; state[`${kind}s`][id] = (state[`${kind}s`][id] || 0) + 1;
      (state.inventoryRefs[kind][id] ||= []).push({ ...originals.get(value.ref), ...value, propsEdited: 'savedProps' in value });
    }
  }
  state.direct = (plan.encounters || []).map((x) => typeof x === 'string' ? x : x.id); state.pool = (plan.pool || []).map((x) => typeof x === 'string' ? x : x.id); state.encounterSource = state.pool.length ? 'pool' : 'direct'; if (plan.search) { Object.assign(state.settings, { ...plan.search, samples: plan.samples || state.settings.samples, poolSeed: plan.poolSeed || state.settings.poolSeed }); if (plan.search.language === 'eng' || plan.search.language === 'zhs') state.language = plan.search.language; } syncEncounterContext(); renderAll();
}

function bindEvents() {
  $$('.nav-item').forEach((button) => button.addEventListener('click', () => setTab(button.dataset.tab)));
  $$('[data-picker]').forEach((button) => button.addEventListener('click', () => openPicker(button.dataset.picker)));
  $('#editStateButton')?.addEventListener('click', () => setTab('build'));
  $('#modalClose').addEventListener('click', closePicker); $('#modalBackdrop').addEventListener('click', (event) => { if (event.target === $('#modalBackdrop')) closePicker(); }); document.addEventListener('keydown', (event) => { if (event.key === 'Escape' && !$('#modalBackdrop').hidden) closePicker(); });
  $('#deckGrid').addEventListener('click', (event) => { const button = event.target.closest('[data-card-action]'); if (button) cardAction(button.dataset.uid, button.dataset.cardAction); });
  $('#deckGrid').addEventListener('change', (event) => { const field = event.target.closest('[data-card-field]'); if (field) updateCard(field.dataset.uid, field.dataset.cardField, field.value); });
  $('#deckGrid').addEventListener('input', (event) => { const field = event.target.closest('input[data-card-field]'); if (field) updateCard(field.dataset.uid, field.dataset.cardField, field.value); });
  $('#deckSearch').addEventListener('input', (event) => { state.filters.deckQuery = event.target.value; renderDeck(); });
  for (const kind of ['relic', 'potion']) $(`#${kind}Grid`).addEventListener('click', (event) => { const button = event.target.closest('[data-inventory-action]'); if (button) adjustInventory(button.dataset.kind, button.dataset.id, button.dataset.inventoryAction); });
  $('#pickerGrid').addEventListener('click', (event) => { const button = event.target.closest('[data-add-kind]'); if (button) { addItem(button.dataset.addKind, button.dataset.id); renderPicker(); } });
  $('#encounterGrid').addEventListener('click', (event) => { const card = event.target.closest('[data-encounter-id]'); if (!card) return; const id = card.dataset.encounterId; if (state.selectedEncounters.has(id)) state.selectedEncounters.delete(id); else state.selectedEncounters.add(id); renderEncounters(); });
  $('#encounterSearch').addEventListener('input', (event) => { state.encounterFilters.search = event.target.value; renderEncounters(); });
  $('#encounterStage').addEventListener('change', (event) => { state.encounterFilters.stage = event.target.value; const act = state.catalog.acts.find((x) => String(x.index) === event.target.value); state.encounterFilters.branch = act?.variants?.find((x) => x.default)?.id || act?.variants?.[0]?.id || ''; renderEncounterFilters(); renderEncounters(); });
  $('#encounterBranch').addEventListener('change', (event) => { state.encounterFilters.branch = event.target.value; renderEncounterSource(); renderEncounters(); });
  $('#encounterMode').addEventListener('change', (event) => { state.encounterFilters.mode = event.target.value; renderEncounterFilters(); renderEncounters(); }); $('#encounterGroupTier').addEventListener('change', (event) => { state.encounterFilters.groupTier = event.target.value; renderEncounterSource(); renderEncounters(); }); $('#encounterPoolPreset').addEventListener('change', (event) => { state.encounterFilters.poolPreset = event.target.value; renderEncounterSource(); renderEncounters(); });
  $$('#quickPoolButtons [data-pool-preset]').forEach((button) => button.addEventListener('click', () => { state.encounterFilters.mode = 'pool'; state.encounterFilters.poolPreset = button.dataset.poolPreset; renderEncounterFilters(); renderEncounters(); })); $('#applyEncounterSource').addEventListener('click', applyEncounterSource); $('#addDirectButton').addEventListener('click', () => { if (!state.selectedEncounters.size) return showToast('请先在当前范围中选择遭遇'); state.direct = Array.from(state.selectedEncounters); state.pool = []; state.encounterSource = 'direct'; renderAll(); showToast('已设为指定遭遇'); }); $('#addPoolButton').addEventListener('click', () => { if (!state.selectedEncounters.size) return showToast('请先在当前范围中选择遭遇'); state.pool = Array.from(state.selectedEncounters); state.direct = []; state.encounterSource = 'pool'; renderAll(); showToast('已将所选遭遇设为自选池'); }); $('#clearSelectionButton').addEventListener('click', () => { state.selectedEncounters.clear(); renderEncounters(); });
  document.addEventListener('click', (event) => { const button = event.target.closest('[data-remove-encounter]'); if (!button) return; const key = button.dataset.list; state[key] = state[key].filter((id) => id !== button.dataset.removeEncounter); renderEncounterLists(); });
  $('#modeSelect').addEventListener('change', (event) => { state.mode = event.target.value; renderAll(); if (!state.save.files.length && state.mode === 'save') refreshSaves(); });
  $('#characterSelect').addEventListener('change', (event) => {
    state.character = event.target.value; state.filters.cardPool = 'CURRENT';
    if (!state.save.bundle) {
      const hp = { IRONCLAD: 80, SILENT: 70, DEFECT: 75, NECROBINDER: 66, REGENT: 75 }[state.character] || 80;
      state.playerStats.hp = hp; state.playerStats.maxHp = hp; state.playerStats.orbSlots = state.character === 'DEFECT' ? 3 : 0;
    }
    markEdited(); renderAll();
  });
  $('#seedInput').addEventListener('input', (event) => { state.seed = event.target.value; markEdited(); });
  $('#ascensionSelect').addEventListener('change', (event) => { state.ascension = Number(event.target.value); markEdited(); renderStateSummary(); });
  $('#actSelect').addEventListener('change', (event) => { state.act = Number(event.target.value); const act = currentAct(); state.actId = act?.variants?.find((x) => x.default)?.id || act?.variants?.[0]?.id || state.actId; markEdited(); syncEncounterContext(); renderAll(); });
  $('#branchSelect').addEventListener('change', (event) => { state.actId = event.target.value; markEdited(); syncEncounterContext(); renderAll(); });
  $$('[data-player-stat]').forEach((input) => input.addEventListener('change', () => {
    const value = Number(input.value); if (!Number.isFinite(value) || value < Number(input.min)) { input.value = state.playerStats[input.dataset.playerStat]; return; }
    state.playerStats[input.dataset.playerStat] = Math.trunc(value); markEdited(); renderContext();
  }));
  $('#localSaveSelect').addEventListener('change', (event) => { state.save.localPath = event.target.value; });
  $('#refreshSavesButton').addEventListener('click', refreshSaves);
  $('#importSaveButton').addEventListener('click', () => importSave({ path: state.save.localPath }));
  $('#savePathInput').addEventListener('input', (event) => { state.save.path = event.target.value; });
  $('#importPathButton').addEventListener('click', () => importSave({ path: state.save.path }));
  $('#uploadSaveButton').addEventListener('click', () => $('#saveFileInput').click());
  $('#saveFileInput').addEventListener('change', async (event) => {
    const file = event.target.files[0]; if (!file) return;
    try {
      if (file.size > 8 * 1024 * 1024) throw new Error('文件超过 8 MiB');
      await importSave({ content: await file.text(), name: file.name });
    } catch (error) { showToast(error.message, 5000); }
    finally { event.target.value = ''; }
  });
  $('#floorInput').addEventListener('change', (event) => selectSaveFloor({ floor: Number(event.target.value) }));
  $('#floorSelect').addEventListener('change', (event) => selectSaveFloor({ floor: Number(event.target.value) }));
  $('#previousFloor').addEventListener('click', () => selectSaveFloor({ floor: state.save.bundle.selection.floor - 1 }));
  $('#nextFloor').addEventListener('click', () => selectSaveFloor({ floor: state.save.bundle.selection.floor + 1 }));
  $$('[data-save-point]').forEach((button) => button.addEventListener('click', () => selectSaveFloor({ point: button.dataset.savePoint, ...(button.dataset.savePoint === 'current' ? { floor: state.save.bundle.source.floors || 1 } : {}) })));
  $('#savePlayerSelect').addEventListener('change', (event) => selectSaveFloor({ player: Number(event.target.value) }));
  $('#restoreSourceButton').addEventListener('click', () => selectSaveFloor({ floor: state.save.bundle.source.floors || 1, point: state.save.bundle.source.kind === 'current' ? 'current' : 'after' }));
  $('#resetFloorButton').addEventListener('click', () => selectSaveFloor({}, { reset: true }));
  $('#floorHistoryRows').addEventListener('click', (event) => { const button = event.target.closest('[data-history-floor]'); if (button) selectSaveFloor({ floor: Number(button.dataset.historyFloor) }); });
  $('#useFloorEncounter').addEventListener('click', () => {
    const ids = state.save.bundle?.selectedFloor?.encounters || []; if (!ids.length) return;
    state.direct = ids.slice(); state.pool = []; state.encounterSource = 'direct'; syncEncounterContext(); setTab('encounter'); renderAll();
  });
  $('#savedPropFields').addEventListener('change', (event) => {
    const input = event.target.closest('[data-saved-prop-group]'); if (!input) return;
    try {
      const props = JSON.parse($('#savedPropsJson').value), group = input.dataset.savedPropGroup;
      const name = input.dataset.savedPropName;
      const values = (props[group] ||= []);
      const index = values.findIndex((p) => p.name === name);
      if (input.dataset.growthProp) {
        if (input.value === '' || !input.checkValidity()) throw new Error('请填写允许范围内的当前卡牌数值');
        setCardGrowth({ ...state.propsTarget, savedProps: props }, Number(input.value));
        $('#savedPropsJson').value = JSON.stringify(props, null, 2); $('#savedPropsError').hidden = true;
        return;
      }
      if (input.dataset.defaultedProp && input.value === '') { if (index >= 0) values.splice(index, 1); }
      else {
        const value = group === 'bools' ? (input.tagName === 'SELECT' ? input.value === 'true' : input.checked) : group === 'strings' ? input.value : Number(input.value);
        if (group === 'ints' && (!Number.isInteger(value) || !input.checkValidity())) throw new Error('请输入允许范围内的整数');
        if (index >= 0) values[index].value = value; else values.push({ name, value });
        state.propsSources[name] = 'manual';
        if (state.propsKind === 'relic' && name === 'IsWax' && !value) {
          const melted = (props.bools || []).find((p) => p.name === 'IsMelted');
          if (melted) melted.value = false;
          state.propsSources.IsMelted = 'manual';
        }
      }
      $('#savedPropsJson').value = JSON.stringify(props, null, 2); $('#savedPropsError').hidden = true;
      if (state.propsKind === 'relic') renderRelicPropFields();
      if (state.propsKind === 'card') {
        const config = cardConfiguration(state.propsTarget, props);
        if (config && name === config.schema.typeProperty && config.rider?.typeValue !== config.typeValue) {
          const rider = config.schema.riders.find((choice) => choice.typeValue === config.typeValue);
          if (rider) {
            const field = values.find((entry) => entry.name === config.schema.riderProperty);
            if (field) field.value = rider.value; else values.push({ name: config.schema.riderProperty, value: rider.value });
            $('#savedPropsJson').value = JSON.stringify(props, null, 2);
          }
        }
        renderCardPropFields();
      }
    } catch (error) { $('#savedPropsError').textContent = error.message; $('#savedPropsError').hidden = false; }
  });
  $('#savedPropFields').addEventListener('click', (event) => {
    const button = event.target.closest('[data-confirm-prop]'); if (!button) return;
    try {
      const props = JSON.parse($('#savedPropsJson').value), field = relicFields(state.propsTarget).find((f) => f.name === button.dataset.confirmProp);
      const value = propValue(props, field), entries = (props[field.group] ||= []);
      if (value == null) throw new Error('请先填写状态值');
      const entry = entries.find((p) => p.name === field.name);
      if (entry) entry.value = value; else entries.push({ name: field.name, value });
      state.propsSources[field.name] = 'manual'; $('#savedPropsJson').value = JSON.stringify(props, null, 2);
      renderRelicPropFields(); $('#savedPropsError').hidden = true;
    } catch (error) { $('#savedPropsError').textContent = error.message; $('#savedPropsError').hidden = false; }
  });
  $('#savedPropsJson').addEventListener('input', () => {
    try {
      const props = JSON.parse($('#savedPropsJson').value);
      if (state.propsKind === 'relic') {
        validateRelicProps(state.propsTarget, props);
        for (const field of relicFields(state.propsTarget)) if ((props[field.group] || []).some((p) => p.name === field.name)) state.propsSources[field.name] = 'manual';
        renderRelicPropFields();
      } else { validateCardConfiguration(state.propsTarget, props); validateCardGrowth(state.propsTarget, props); renderCardPropFields(); }
      $('#savedPropsError').hidden = true;
    } catch (error) { $('#savedPropsError').textContent = error.message; $('#savedPropsError').hidden = false; }
  });
  $('#applySavedProps').addEventListener('click', () => {
    try {
      const props = JSON.parse($('#savedPropsJson').value);
      if (!props || Array.isArray(props) || typeof props !== 'object') throw new Error('保存属性必须是 JSON 对象');
      if (state.propsKind === 'relic') {
        if ($$('#savedPropFields input').some((input) => !input.checkValidity())) throw new Error('请修正超出范围的状态值');
        validateRelicProps(state.propsTarget, props); state.propsTarget.propertySources = { ...state.propsSources };
      }
      if (state.propsKind === 'card') {
        if ($$('#savedPropFields input').some((input) => !input.checkValidity())) throw new Error('请先填写有效的卡牌数值');
        validateCardConfiguration(state.propsTarget, props); validateCardGrowth(state.propsTarget, props);
        const growth = cardGrowth(state.propsTarget, props);
        if (growth) setCardGrowth({ ...state.propsTarget, savedProps: props }, growth.value);
        delete state.propsTarget.growthDraft;
        state.propsTarget.defaultedProps = (state.propsTarget.defaultedProps || []).filter((field) => !(props[field.group] || []).some((property) => property.name === field.name));
      }
      state.propsTarget.savedProps = props; state.propsTarget.propsEdited = true; markEdited(); $('#savedPropsDialog').close(); renderAll();
    } catch (error) { $('#savedPropsError').textContent = error.message; $('#savedPropsError').hidden = false; }
  });
  $('#runButton').addEventListener('click', startEvaluation); $('#stopButton').addEventListener('click', async () => { if (state.jobId) await fetch(`/api/jobs/${state.jobId}/stop`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }); }); $('#savePlanButton').addEventListener('click', savePlan); $('#loadPlanButton').addEventListener('click', loadPlan); $('#profileSelect').addEventListener('change', (event) => { const budgets = { Low: 60000, Medium: 120000, High: 180000, VeryHigh: 300000 }; state.settings.profile = event.target.value; if (budgets[event.target.value]) { state.settings.budgetMs = budgets[event.target.value]; $('#budgetInput').value = budgets[event.target.value] / 1000; } renderRuntimeEstimate(); }); $('#languageSelect').addEventListener('change', (event) => { state.language = event.target.value === 'eng' ? 'eng' : 'zhs'; state.settings.language = state.language; renderAll(); }); $('#languageToggle').addEventListener('click', () => { state.language = state.language === 'zhs' ? 'eng' : 'zhs'; state.settings.language = state.language; renderAll(); });
  ['samplesInput', 'dopInput', 'caseWorkersInput'].forEach((id) => { const field = $(`#${id}`); field?.addEventListener('input', () => { collectSettings(); renderRuntimeEstimate(); }); });
}

async function boot() {
  try {
    const response = await fetch('/api/catalog'); state.catalog = await response.json();
    $('#catalogMeta').textContent = `游戏目录 · ${state.catalog.catalogVersion || '本地'} · ${Object.keys(state.catalog.cards || {}).length} 张牌`; $('#footerVersion').textContent = `目录版本 ${state.catalog.catalogVersion || '—'}`; $('#harnessState').textContent = '在线';
    const health = await fetch('/api/health').then((r) => r.json()); if (!health.harness) { $('#harnessState').textContent = '未构建'; showToast('OfflineSearchHarness 尚未构建，网页仍可编辑方案。', 6000); }
    bindEvents(); renderAll(); refreshSaves();
  } catch (error) { $('#harnessState').textContent = '离线'; showToast(`网页服务初始化失败：${error.message}`, 8000); }
}
boot();
