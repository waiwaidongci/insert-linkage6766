import {feedback, field} from '../components/common/fields.js';
import {empty, metrics, panel, pill} from '../components/common/panels.js';
import {table} from '../components/common/table.js';
import {CHANGE_ACTION, CYCLE_STATE, ORDER_STATE} from '../constants/enums.js';
import {handler} from '../hooks/action.js';
import {session} from '../stores/session.js';
import {workspace} from '../stores/workspace.js';
import {button, el, h, link, p} from '../utils/dom.js';
import {date} from '../utils/format.js';

const FLAGS = {duplicate:'相同内容已收到过，按原记录处理。', conflict:'与同号已收内容冲突，等待厂方裁定。',
  held:'已暂存，等待前面缺号补齐。', ready:'已就位，可参与交割。', applied:'已交割。', void:'已作废。'};
let flash = '';

const factoryLabel = (spec, key) => spec.factories.find(item => item.key === key)?.label || key;
const selectField = (key, label, entries, help) => el('label', {class:'field', for:`field-${key}`},
  el('span', {}, label),
  el('select', {id:`field-${key}`, name:key, required:true},
    entries.map(([value, text]) => el('option', {value}, text))),
  help && el('small', {}, help));

function orderForm(spec, refresh) {
  const error = feedback();
  const submit = button('提交变更单', {type:'submit', class:'primary', 'data-testid':'submit-order'});
  return el('form', {class:'fields', onSubmit:handler(submit, error, async event => {
    const data = new FormData(event.target);
    const result = await workspace.submitOrder({
      factory:data.get('factory'), order_no:Number(data.get('order_no')), action:data.get('action'),
      spec_id:data.get('spec_id'), name:data.get('name'), fit_traits:data.get('fit_traits')});
    flash = `变更单 ${result.order_no} 号：${FLAGS[result.flag] || result.flag}`;
    await refresh();
  })}, selectField('factory', '厂别', spec.factories.map(item => [item.key, item.label])),
    field({key:'order_no', label:'变更单号', type:'int', min:1, help:'按厂各自从 1 起连续编号'}),
    selectField('action', '变更动作', Object.entries(CHANGE_ACTION)),
    field({key:'spec_id', label:'规格编号', type:'string', max_length:80}),
    field({key:'name', label:'内衬名称（撤销时可空）', type:'string', required:false, max_length:120}),
    field({key:'fit_traits', label:'必需适配特征（用 | 分隔，撤销时可空）', type:'string', required:false, max_length:600}),
    submit, error);
}

function noticeForm(spec, refresh) {
  const error = feedback();
  const submit = button('提交交割号', {type:'submit', class:'primary', 'data-testid':'submit-notice'});
  return el('form', {class:'fields', onSubmit:handler(submit, error, async event => {
    const data = new FormData(event.target);
    const result = await workspace.submitNotice({
      factory:data.get('factory'), delivery_no:Number(data.get('delivery_no')),
      covers_through:Number(data.get('covers_through'))});
    flash = `交割号 ${result.delivery_no} 号：${FLAGS[result.flag] || result.flag}`;
    await refresh();
  })}, selectField('factory', '厂别', spec.factories.map(item => [item.key, item.label])),
    field({key:'delivery_no', label:'交割号', type:'int', min:1, help:'按厂各自从 1 起连续编号'}),
    field({key:'covers_through', label:'覆盖至变更单号', type:'int', min:1,
      help:'声明本厂变更单到该编号为止已全部发出'}),
    submit, error);
}

function conflictPanel(spec, board, refresh) {
  const error = feedback();
  const groups = [];
  for (const factory of spec.factories) {
    const progress = board.factories.find(item => item.key === factory.key);
    for (const orderNo of progress?.conflicts || []) {
      const rows = board.orders.filter(row => row.factory === factory.key && row.order_no === orderNo && row.conflict);
      groups.push({factory:factory.key, number:orderNo, rows,
        title:`${factory.label} · 变更单 ${orderNo} 号`,
        describe:row => `${CHANGE_ACTION[row.action]} ${row.spec_id} · ${row.name || '—'} · ${row.fit_traits || '—'}`,
        rule:keep_id => workspace.ruleOrder({factory:factory.key, order_no:orderNo, keep_id})});
    }
  }
  const noticed = new Set();
  for (const notice of board.notices.filter(row => row.conflict)) {
    const key = `${notice.factory}/${notice.delivery_no}`;
    if (noticed.has(key)) continue;
    noticed.add(key);
    const rows = board.notices.filter(row => row.factory === notice.factory
      && row.delivery_no === notice.delivery_no && row.conflict);
    groups.push({factory:notice.factory, number:notice.delivery_no, rows,
      title:`${factoryLabel(spec, notice.factory)} · 交割号 ${notice.delivery_no} 号`,
      describe:row => `覆盖至变更单 ${row.covers_through} 号`,
      rule:keep_id => workspace.ruleNotice({factory:notice.factory, delivery_no:notice.delivery_no, keep_id})});
  }
  if (!groups.length) return panel('待裁定冲突', p('当前没有待厂方裁定的内容冲突。', 'subtle'));
  return panel('待裁定冲突', p('同厂同号收到不同内容，由复核主管登记厂方裁定，其余记录作废。', 'subtle'),
    groups.map(group => el('div', {class:'decision'},
      h(3, group.title),
      el('ul', {class:'method-list'}, group.rows.map(row => el('li', {},
        `${group.describe(row)} · ${row.author} · ${date(row.created_at)} `,
        session.canReview && button('裁定采用', {class:'quiet', onClick:event => {
          const control = event.currentTarget;
          handler(control, error, async () => {
            await group.rule(row.id);
            flash = `已登记厂方裁定：${group.title} 采用 ${row.author} 提交的版本`;
            await refresh();
          })(event);
        }})))))),
    error);
}

function cycleTable(spec, board, refresh) {
  const error = feedback();
  const cover = (row, key) => row.covers[key]
    ? `交割号 ${row.covers[key].delivery_no} · 覆盖至 ${row.covers[key].covers_through} 号` : '—';
  const grid = table([
    {label:'期号', render:row => `第 ${row.id} 期`},
    {label:spec.factories[0].label, render:row => cover(row, spec.factories[0].key)},
    {label:spec.factories[1].label, render:row => cover(row, spec.factories[1].key)},
    {label:'状态', render:row => pill(CYCLE_STATE[row.state], row.state)},
    {label:'说明', render:row => row.issues?.length ? row.issues.join('；') : row.detail || '—'},
    {label:'归并批次', render:row => {
      if (!row.dataset_id) return '—';
      return el('span', {}, link(`批次 ${row.dataset_id}`, `#inputs/${row.dataset_id}`),
        row.is_current && pill('当前', 'ready'));
    }},
    {label:'操作', render:row => {
      if (row.state !== 'failed' || !session.canReview) return '—';
      const retry = button('重试交割', {class:'quiet'});
      retry.addEventListener('click', handler(retry, error, async () => {
        await workspace.retryCycle(row.id);
        flash = `第 ${row.id} 期已重新排队交割`;
        await refresh();
      }));
      return retry;
    }},
  ], board.cycles, {caption:'交割周期', empty:'两厂交割号就位后会自动配对成周期。'});
  return el('div', {}, grid, error);
}

export async function settlementPage(route, refresh) {
  const {spec} = workspace;
  const board = await workspace.settlementBoard();
  const note = flash;
  flash = '';
  const current = board.current;
  const overview = panel('当前归并批次',
    current ? el('div', {},
      metrics([{label:'归并批次', value:current.name},
        {label:'生效规格', value:current.row_count, detail:'条'}]),
      el('div', {class:'actions'}, link('查看生效规格与来源', `#inputs/${current.dataset_id}`, {class:'quiet'})))
      : empty('还没有可归并的批次。', '#inputs', '前往导入'),
    p('已排队的归并仍按原批次计算，新批次只影响之后提交的运行。', 'subtle'),
    note && p(note, 'feedback'));
  const factories = panel('两厂签收进度', table([
    {key:'label', label:'厂别'},
    {key:'applied', label:'已交割'},
    {key:'ready', label:'已就位'},
    {key:'held', label:'暂存待补'},
    {label:'待裁定冲突', render:row => row.conflicts.length ? row.conflicts.join('、') : '—'},
  ], board.factories, {caption:'两厂签收进度'}));
  const orders = panel('变更单记录', table([
    {label:'厂别', render:row => factoryLabel(spec, row.factory)},
    {key:'order_no', label:'单号'},
    {label:'动作', render:row => CHANGE_ACTION[row.action]},
    {key:'spec_id', label:'规格编号'},
    {label:'内容', render:row => row.action === 'revoke' ? '撤销该规格' : `${row.name}｜${row.fit_traits}`},
    {label:'状态', render:row => el('span', {}, pill(ORDER_STATE[row.state], row.state),
      row.conflict && pill('待裁定', 'held'), row.ruled && pill('裁定采用', 'ready'))},
    {label:'到达时间', render:row => date(row.created_at)},
  ], board.orders, {caption:'变更单记录', empty:'还没有收到变更单。'}));
  const notices = panel('交割号记录', table([
    {label:'厂别', render:row => factoryLabel(spec, row.factory)},
    {key:'delivery_no', label:'交割号'},
    {key:'covers_through', label:'覆盖至变更单号'},
    {label:'状态', render:row => el('span', {}, pill(ORDER_STATE[row.state], row.state),
      row.conflict && pill('待裁定', 'held'))},
    {label:'配对', render:row => row.cycle_id ? `第 ${row.cycle_id} 期` : '未配对'},
  ], board.notices, {caption:'交割号记录', empty:'还没有收到交割号。'}));
  const cycles = panel('交割周期', cycleTable(spec, board, refresh));
  const submit = session.canWrite && el('div', {},
    panel('提交变更单', p('变更单可新增、更正或撤销规格，内容指纹随记录保留。', 'subtle'), orderForm(spec, refresh)),
    panel('提交交割号', p('交割号声明本厂变更单的覆盖范围，两厂配对后进入交割周期。', 'subtle'), noticeForm(spec, refresh)));
  return el('div', {class:'two-col'},
    el('div', {class:'wide-panel'}, overview, factories, cycles, orders, notices),
    el('aside', {class:'sidebar-panel'},
      panel('交割口径', el('ul', {class:'method-list'}, spec.settlement_rules.map(text => el('li', {}, text)))),
      conflictPanel(spec, board, refresh),
      submit || p('只读角色可以查看两厂签收与交割进度。', 'empty')));
}
