// Node unit tests for the card scanner's parser and search index (run by tests/test_card_scanner.py).
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const web = path.join(__dirname, '..', '..', 'card_scanner', 'web');
const P = require(path.join(web, 'parser.js'));
const S = require(path.join(web, 'search.js'));

const TW = `鑫科材料科技股份有限公司
Thintech Materials Technology Co., Ltd.
王大明 業務經理
David Wang
業務部
T: 06-5052-690 #123  F: 06-5052-691
M: 0912-345-678
E-mail: david.wang @ thintech.com.tw
台南市新市區南科三路 15 號 3 樓
www.thintech.com.tw
統一編號: 12345678`;

test('parses a Taiwanese bilingual card', () => {
  const f = P.parseCardText(TW).fields;
  assert.equal(f.name, '王大明');
  assert.equal(f.nameEn, 'David Wang');
  assert.equal(f.title, '業務經理');
  assert.equal(f.department, '業務部');
  assert.equal(f.company, '鑫科材料科技股份有限公司');
  assert.equal(f.companyEn, 'Thintech Materials Technology Co., Ltd.');
  assert.equal(f.tel, '06-5052-690 #123');
  assert.equal(f.fax, '06-5052-691');
  assert.equal(f.mobile, '0912-345-678');
  assert.equal(f.email, 'david.wang@thintech.com.tw');
  assert.equal(f.address, '台南市新市區南科三路 15 號 3 樓');
  assert.equal(f.website, 'www.thintech.com.tw');
  assert.equal(f.taxId, '12345678');
});

test('parses an English card without mistaking single letters for labels', () => {
  const f = P.parseCardText('ACME Corp.\nJane Doe\nSenior Sales Manager\n+1 (415) 555-0100\njane.doe@acme.com\n1 Market Street, Suite 300, San Francisco, CA').fields;
  assert.equal(f.company, 'ACME Corp.');
  assert.equal(f.name, 'Jane Doe');
  assert.equal(f.title, 'Senior Sales Manager');
  assert.equal(f.tel, '+1 (415) 555-0100'); // international number is not a mobile
  assert.equal(f.address, '1 Market Street, Suite 300, San Francisco, CA');
});

test('joins OCR spaces between CJK characters and full-width digits', () => {
  const f = P.parseCardText('台 灣 積 體 電 路 製 造 股 份 有 限 公 司\n林 志 明\n資 深 經 理\n手機：０９２８－１１１２２２').fields;
  assert.equal(f.company, '台灣積體電路製造股份有限公司');
  assert.equal(f.name, '林志明');
  assert.equal(f.title, '資深經理');
  assert.equal(f.mobile, '0928-111222');
});

const cards = [
  { id: 'a', ...P.parseCardText(TW).fields, tags: ['客戶'], notes: '展覽認識' },
  { id: 'b', name: 'Jane Doe', company: 'ACME Corp.', title: 'Sales Manager', tel: '+1 (415) 555-0100', email: 'jane.doe@acme.com', tags: ['海外'] },
  { id: 'c', name: '陳小華', company: '台灣積體電路製造股份有限公司', title: '資深工程師', mobile: '0928-111222', tags: ['客戶', '半導體'] },
];
const ids = (ix, q) => ix.search(q).map(r => r.card.id);

test('search: CJK substrings, prefixes, phone suffixes, fields, tags, negation', () => {
  const ix = new S.CardIndex(cards);
  assert.deepEqual(ids(ix, '大明'), ['a']);
  assert.deepEqual(ids(ix, '積體'), ['c']);
  assert.deepEqual(ids(ix, 'thin'), ['a']);
  assert.deepEqual(ids(ix, '5678'), ['a']);           // mobile 0912-345-678
  assert.deepEqual(ids(ix, '0100'), ['b']);
  assert.deepEqual(ids(ix, '#客戶').sort(), ['a', 'c']);
  assert.deepEqual(ids(ix, '公司:acme'), ['b']);
  assert.deepEqual(ids(ix, 'company:台灣'), ['c']);
  assert.deepEqual(ids(ix, '#客戶 -半導體'), ['a']);
  assert.deepEqual(ids(ix, '"sales manager"'), ['b']);
  assert.deepEqual(ids(ix, '展覽'), ['a']);            // notes are searchable
  assert.deepEqual(ids(ix, '12345678'), ['a']);        // tax id (digits outside phone fields)
  assert.deepEqual(ids(ix, '不存在'), []);
  assert.equal(ix.search('').length, 3);
});

test('search: name hits rank above incidental hits, index stays in sync', () => {
  const ix = new S.CardIndex([
    { id: 'x', name: '王小明', company: '甲公司' },
    { id: 'y', name: '李四', company: '王氏企業' },
  ]);
  assert.deepEqual(ids(ix, '王'), ['x', 'y']);
  ix.remove('x');
  assert.deepEqual(ids(ix, '王'), ['y']);
  ix.add({ id: 'y', name: '李四', company: '乙公司' }); // update replaces old tokens
  assert.deepEqual(ids(ix, '王'), []);
  assert.equal(ix.size, 1);
});

test('grouping sorts Latin initials first, then CJK by stroke order', () => {
  const g = S.groupCards([{ name: '陳' }, { name: 'bob' }, { name: '王' }, { name: '' , company: '' }, { name: 'Amy' }], 'name');
  assert.deepEqual(g.map(x => x.key), ['A', 'B', '王', '陳', '#']);
});

test('highlight escapes HTML and marks phone digits across separators', () => {
  assert.equal(S.highlight('0912-345-678', '345678'), '0912-<mark>345-678</mark>');
  assert.equal(S.highlight('<b>鑫科</b>', '鑫科'), '&lt;b&gt;<mark>鑫科</mark>&lt;/b&gt;');
});

test('splits two-column OCR lines on wide gaps', () => {
  const f = P.parseCardText('星河光電股份有限公司\n林雅婷                               M: 0912-345-678\n採購部 資深採購專員                         T: 03-578-1234 #215').fields;
  assert.equal(f.name, '林雅婷');
  assert.equal(f.mobile, '0912-345-678');
  assert.equal(f.department, '採購部');
  assert.equal(f.title, '資深採購專員');
  assert.equal(f.tel, '03-578-1234 #215');
});
