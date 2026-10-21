// 只在隔离的本机合成样件上运行；两个任务均须是未回答的新底稿。
export async function verifyAnswerAnnotations(page, {url, token, jobId, nextJobId}) {
  await page.goto(url);
  await page.click('#task-section .section-toggle');
  await page.fill('#token', token);
  await page.focus('#stage');
  await page.waitForFunction(id => !!document.querySelector(`#jobs li[data-job="${id}"] button`), jobId);
  await page.focus(`#jobs li[data-job="${jobId}"] button`);
  await page.press(`#jobs li[data-job="${jobId}"] button`, 'Enter');
  await page.waitForFunction(() => !!document.querySelector('#drawing svg')
    && document.querySelectorAll('[data-question-card]').length > 0);
  await page.focus('#who');
  await page.fill('#who', '变更定位演练（非人工制图审核）');
  const questions = await page.evaluate(() => [...document.querySelectorAll('[data-question-card]')]
    .map(card => ({id: card.dataset.question, hasH7: !!card.querySelector('input[value="H7"]')})));
  let selected = false;
  for (const question of questions) {
    const card = `[data-question-card][data-question="${question.id}"]`;
    if (question.hasH7 && !selected) {
      await page.focus(`${card} input[value="H7"]`);
      await page.press(`${card} input[value="H7"]`, 'Space');
      await page.focus(`${card} [data-action=confirm]`);
      await page.press(`${card} [data-action=confirm]`, 'Enter');
      selected = true;
    } else {
      await page.focus(`${card} [data-action=unknown]`);
      await page.press(`${card} [data-action=unknown]`, 'Space');
    }
  }
  if (!selected) throw new Error('合成样件需要至少一个 H7 候选');
  await page.focus('#submit-answers');
  await page.press('#submit-answers', 'Enter');
  await page.waitForFunction(() => document.querySelector('#version-select').value === '1'
    && document.querySelector('#questions-empty').hidden === false);
  const status = (await page.fetch(`/jobs/${jobId}`, {headers: {Authorization: 'Bearer ' + token}})).body;
  const job = typeof status === 'string' ? JSON.parse(status) : status;
  const response = (await page.fetch(job.files.json, {headers: {Authorization: 'Bearer ' + token}})).body;
  const document = typeof response === 'string' ? JSON.parse(response) : response;
  if (document.annotations.length !== 2) throw new Error('后端应有尺寸和标题栏两处文字变更');
  const measurements = [];
  async function measure(label) {
    const result = await page.evaluate(({annotations, label}) => {
      const paper = document.querySelector('#paper').getBoundingClientRect();
      const svg = document.querySelector('#drawing svg'), view = svg.viewBox.baseVal;
      const rings = [...document.querySelectorAll('#annots .ring')];
      return {label, count: rings.length,
        pointerEvents: document.querySelector('#annots')
          ? getComputedStyle(document.querySelector('#annots')).pointerEvents : null,
        errors: rings.map((ring, i) => {
          const rect = ring.getBoundingClientRect(), box = annotations[i].box;
          return Math.max(Math.abs(rect.left - paper.left - box[0] / view.width * paper.width),
            Math.abs(rect.top - paper.top - box[1] / view.height * paper.height),
            Math.abs(rect.width - box[2] / view.width * paper.width),
            Math.abs(rect.height - box[3] / view.height * paper.height));
        })};
    }, {annotations: document.annotations, label});
    if (result.count !== 2 || result.pointerEvents !== 'none' || result.errors.some(n => n > 1.5)) {
      throw new Error('变更提示与服务端坐标未对齐：' + JSON.stringify(result));
    }
    measurements.push(result);
  }
  await measure('fit');
  await page.focus('#zoomctl [data-z="+"]');
  await page.press('#zoomctl [data-z="+"]', 'Enter');
  await measure('zoomed');
  await page.cdp('Emulation.setDeviceMetricsOverride', {width: 390, height: 844, deviceScaleFactor: 1, mobile: false});
  await page.focus('#zoomctl [data-z="fit"]');
  await page.press('#zoomctl [data-z="fit"]', 'Enter');
  await measure('390px');
  await page.cdp('Emulation.setDeviceMetricsOverride', {width: 1440, height: 900, deviceScaleFactor: 1, mobile: false});
  await page.focus('#vb-before');
  await page.press('#vb-before', 'Enter');
  await page.waitForFunction(() => document.querySelector('#version-select').value === '0');
  if (await page.evaluate(() => document.querySelectorAll('#annots .ring').length !== 0)) {
    throw new Error('底稿残留了答案版的变更提示');
  }
  await page.focus('#vb-after');
  await page.press('#vb-after', 'Enter');
  await page.waitForFunction(() => document.querySelector('#version-select').value === '1');
  await measure('return-current');
  if (await page.evaluate(() => document.querySelector('#task-section').dataset.open !== '1')) {
    await page.focus('#task-section .section-toggle');
    await page.press('#task-section .section-toggle', 'Enter');
  }
  await page.focus(`#jobs li[data-job="${nextJobId}"] button`);
  await page.press(`#jobs li[data-job="${nextJobId}"] button`, 'Enter');
  await page.waitForFunction(id => document.querySelector('#part').textContent === id
    && !!document.querySelector('#drawing svg'), nextJobId);
  if (await page.evaluate(() => document.querySelectorAll('#annots .ring').length !== 0)) {
    throw new Error('切换任务后残留了其他任务的变更提示');
  }
  return {jobId, nextJobId, measurements, originalCleared: true, nextJobCleared: true};
}
