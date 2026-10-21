// 在隔离的本机服务上运行；jobId 必须是未回传过 DXF 的合成样件任务。
// originalDxf 是该任务底稿，wrongDxf 是只改一条尺寸显示值的测试副本。
export async function verifyFrozenNotice(page, {url, token, jobId, originalDxf, wrongDxf}) {
  await page.goto(url);
  await page.click('#task-section .section-toggle');
  await page.fill('#token', token);
  await page.focus('#stage');
  await page.waitForFunction(id => !!document.querySelector(`#jobs li[data-job="${id}"] button`), jobId);
  const task = `#jobs li[data-job="${jobId}"] button`;
  await page.focus(task);
  await page.press(task, 'Enter');
  await page.waitForFunction(() => !!document.querySelector('#drawing svg'));
  if (await page.evaluate(() => document.querySelector('#version-select').options.length !== 1
    || !document.querySelector('#state-notice').hidden)) {
    throw new Error('回归用例需要一张尚未回传过的新底稿');
  }
  await page.focus('#who');
  await page.fill('#who', '本地冻结提示回归（非人工制图审核）');
  await page.focus('#review-section .section-toggle');
  await page.press('#review-section .section-toggle', 'Space');
  const results = [];
  for (const [version, file, expectedFrozen] of [[1, originalDxf, false], [2, wrongDxf, true]]) {
    await page.setInputFiles('#amended-file', [file]);
    await page.focus('#amend button');
    await page.press('#amend button', 'Enter');
    await page.waitForFunction(n => document.querySelector('#diff').textContent.includes(`已生效（第 ${n} 版）`)
      && !document.querySelector('#amend button').disabled, version);
    const result = await page.evaluate(() => ({
      frozen: document.querySelector('#state-notice').classList.contains('t-frozen'),
      notice: document.querySelector('#notice-title').textContent,
      acceptDisabled: document.querySelector('#rvBtns [data-d="accepted"]').disabled,
      amendEnabled: !document.querySelector('#amend button').disabled,
      diff: document.querySelector('#diff').textContent,
    }));
    if (result.frozen !== expectedFrozen || result.acceptDisabled !== expectedFrozen || !result.amendEnabled) {
      throw new Error(`第 ${version} 版冻结提示不符合接口的布尔值语义：${JSON.stringify(result)}`);
    }
    results.push(result);
  }
  return results;
}
