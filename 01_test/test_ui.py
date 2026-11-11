"""画面の確認。本物のサーバーを起動し、スマホ幅 (ダーク) と PC 幅で操作してスクリーンショットを残す。

    CHROMIUM_PATH=<chrome> <playwright の入った python> 01_test/test_ui.py

API キーなしで起動し、案内が出ること・横にはみ出さないこと・コンソールにエラーがないことを確かめる。
"""
import os, sys, subprocess, time, shutil, sqlite3, json
from pathlib import Path
from playwright.sync_api import sync_playwright, expect
root=Path(__file__).resolve().parents[1]; shots=root/'01_test/screenshots'; shots.mkdir(exist_ok=True)
data=root/'01_test/data_ui'; shutil.rmtree(data,ignore_errors=True); data.mkdir()
env={**os.environ,'MATDEV_DATA_DIR':str(data/'matdev'),'MATDEV_WORKS_ROOT':str(data/'works'),'MATSIM_DATA_DIR':str(data/'matsim'),'MATDEV_PORT':'8047','MATDEV_MONTHLY_LIMIT_USD':'1'}
env.pop('ANTHROPIC_API_KEY',None)
srv=subprocess.Popen([str(Path.home()/'.venv/matdev312/bin/python'),'-m','uvicorn','matdev.main:app','--port','8047'],cwd=root,env=env,stdout=open(data/'server.log','w'),stderr=subprocess.STDOUT)
try:
    for _ in range(100):
        try:
            import urllib.request; urllib.request.urlopen('http://127.0.0.1:8047/api/status'); break
        except Exception: time.sleep(.2)
    with sync_playwright() as q:
        b=q.chromium.launch(executable_path=os.environ['CHROMIUM_PATH'],args=['--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader'])
        errs=[]
        for name,w,h,scheme in [('phone',400,800,'dark'),('pc',1280,800,'dark')]:
            c=b.new_context(viewport={'width':w,'height':h},color_scheme=scheme); p=c.new_page()
            p.on('console',lambda m: errs.append(m.text) if m.type=='error' else None); p.on('pageerror',lambda e: errs.append(str(e)))
            p.goto('http://127.0.0.1:8047/')
            expect(p.locator('#auth-submit')).to_be_visible()
            if name=='phone':
                p.screenshot(path=shots/'01_setup_phone.png')
                p.fill('#username','tester');p.fill('#password','password-123');p.click('#auth-submit')
            else:
                p.fill('#username','tester');p.fill('#password','password-123');p.click('#auth-submit')
            expect(p.locator('#banner')).to_be_visible()
            p.fill('#input','Ti の相転移を調べて')
            p.click('#send')
            # 会話を始める前に出力先 (作業) を決める
            p.wait_for_timeout(800)
            if p.locator('dialog[open]').count():   # 会話がまだない (最初の 1 回)
                p.locator('dialog[open] input[placeholder^="新しい作業"]').fill('試験の作業')
                p.locator('dialog[open] button:has-text("決める")').click()
            expect(p.locator('#work-chip')).to_have_text('試験の作業')
            expect(p.locator('.notice.error').first).to_contain_text('鍵がありません', timeout=15000)
            expect(p.locator('#title')).to_have_text('Ti の相転移を調べて')
            p.wait_for_timeout(500)
            print(name,p.evaluate('[document.documentElement.scrollWidth,innerWidth]'),p.locator('.notice.error').first.inner_text())
            p.screenshot(path=shots/f'02_chat_{name}.png')
            if name=='phone':
                p.click('#menu'); p.wait_for_timeout(300); p.screenshot(path=shots/'03_menu_phone.png'); p.click('#scrim', position={'x': 390, 'y': 400})
                p.click('#open-panel'); p.wait_for_timeout(300); expect(p.locator('#panel-body')).to_contain_text('まだ道具を使っていません')
                p.click('[data-tab=notes]'); expect(p.locator('#panel-body')).to_contain_text('ノートはまだありません')
                p.screenshot(path=shots/'04_notes_phone.png'); p.click('#close-panel')
            else:
                expect(p.locator('#panel')).to_be_visible(); expect(p.locator('#open-panel')).to_be_hidden()   # PC では資料を常に出す
                p.fill('#assistant-name', 'フライデー'); p.press('#assistant-name', 'Enter'); p.wait_for_timeout(500)
                expect(p.locator('#input')).to_have_attribute('placeholder', 'フライデーへの依頼')
                p.click('#open-space'); p.wait_for_timeout(1500); expect(p.locator('#space-canvas')).to_be_visible()
                p.screenshot(path=shots/'05_space_pc.png'); p.click('#close-space')
                n = p.locator('.chat-row').count()
                p.locator('.chat-row .remove').first.click(); expect(p.locator('#delete-dialog')).to_be_visible()
                p.check('#delete-materials'); p.click('#delete-dialog button[value=delete]'); p.wait_for_timeout(800)
                assert p.locator('.chat-row').count() == n - 1
            c.close()
        print('errors',errs); b.close()
        assert not errs
finally:
    srv.terminate(); srv.wait(); print(open(data/'server.log').read()[-400:])
