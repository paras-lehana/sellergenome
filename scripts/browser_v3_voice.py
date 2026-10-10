"""V3 password-session provider/browser test using synthetic microphone audio.

This exercises real WS/STT/TTS, not hardware acoustics or a human gold corpus.
"""

import argparse
import base64
import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def run(
    base,
    width,
    height,
    engine,
    key,
    output,
    fixture,
    live_only=False,
    use_llm=False,
    portal_path="",
):
    portal = base.rstrip("/") + portal_path.rstrip("/")
    with sync_playwright() as playwright:
        browser = getattr(playwright, engine).launch(headless=True)
        context = browser.new_context(
            viewport={"width": width, "height": height},
            reduced_motion="reduce",
            is_mobile=width < 600,
            has_touch=width < 600,
        )
        page = context.new_page()
        errors, events, requests = [], [], []
        page.on("request", lambda request: requests.append(request.url))
        page.on("pageerror", lambda error: errors.append(str(error)))
        # Controlled mic stream, actual voice fixture, real upstream recognition.
        context.add_init_script(
            """(() => {
          const encoded = %s;
          window.__micTracks=[];window.__micStage='installed';
          const syntheticMicrophone=async()=>{
            window.__micStage='capture-context';const audio=new AudioContext();await audio.resume();window.__micStage='capture-resumed';
            const prototype=Object.getPrototypeOf(audio.audioWorklet),add=prototype.addModule;window.__workletUrls=[];
            Object.defineProperty(prototype,'addModule',{value:function(url,...args){window.__workletUrls.push(String(url));return add.call(this,url,...args);},configurable:true});
            const bytes=Uint8Array.from(atob(encoded),c=>c.charCodeAt(0));
            const buffer=await audio.decodeAudioData(bytes.buffer);
            const source=audio.createBufferSource();source.buffer=buffer;
            const dest=audio.createMediaStreamDestination();window.__micStage='capture-destination';source.connect(dest);
            window.__startSyntheticMicrophone=()=>source.start(audio.currentTime+3);
            for(const track of dest.stream.getTracks()){
              const original=track.stop.bind(track);track.stop=()=>{original();audio.close().catch(()=>{});};
              window.__micTracks.push(track);
            }
            window.__micStage='returned';return dest.stream;
          };
          Object.defineProperty(navigator,'mediaDevices',{value:{getUserMedia:syntheticMicrophone},configurable:true});
        })();"""
            % json.dumps(base64.b64encode(fixture.read_bytes()).decode())
        )
        page.on(
            "websocket",
            lambda socket: socket.on(
                "framereceived",
                lambda data: events.append(json.loads(data))
                if isinstance(data, str) and data.startswith("{")
                else None,
            ),
        )
        page.goto(portal + "/#speech", wait_until="networkidle")
        expect(
            page.get_by_role(
                "heading", name="Hear the difference. Trace the words."
            )
        ).to_be_visible()
        expect(page.locator("audio")).to_have_count(6)
        assert context.request.get(base + "/api/speech-jobs").status == 401
        clip = page.locator("audio").first
        clip.evaluate("audio => audio.load()")
        page.wait_for_function(
            "() => document.querySelector('audio').readyState>=2",
            timeout=20000,
        )
        clip.evaluate("audio => audio.play()")
        page.wait_for_function(
            "() => document.querySelector('audio').currentTime>.3"
        )
        clip.evaluate("audio => audio.pause()")
        page.screenshot(
            path=str(
                output
                / f"{engine}-{width}-{base.split('//')[1].split('/')[0]}-voices.png"
            ),
            full_page=True,
        )
        assert page.evaluate(
            "document.documentElement.scrollWidth<=innerWidth+1"
        )
        signed = context.request.post(
            base + "/api/auth/login", data={"password": key}
        )
        assert signed.status == 200, "Password login failed"
        page.reload(wait_until="networkidle")
        if not live_only:
            page.goto(portal + "/#speech", wait_until="networkidle")
            expect(page.locator("#speech-upload")).to_be_visible()
            page.locator("#speech-file").set_input_files(fixture)
            page.locator("#speech-date").fill("2026-10-01T12:00")
            page.locator("#speech-language").select_option("en-IN")
            page.locator("#speech-speakers").fill("1")
            page.locator("#speech-upload input[name=synthetic]").check()
            page.locator("#speech-upload input[name=consent]").check()
            with page.expect_response(
                lambda response: response.url.endswith("/api/speech-jobs")
                and response.request.method == "POST"
            ) as queued:
                page.get_by_role(
                    "button", name="Queue transcription & diarization"
                ).click()
            assert queued.value.status == 202, queued.value.json()
            job_id = queued.value.json()["id"]
            assert (
                context.request.get(base + "/api/speech-jobs/" + job_id).status
                == 200
            )
            expect(page.locator("#speech-review .badge")).to_have_text(
                "Completed", timeout=120000
            )
            expect(page.locator(".transcript-segment").first).to_be_visible()
            expect(page.locator("#speech-review")).to_contain_text(
                "cannot become real seller evidence"
            )
            expect(
                page.get_by_role("button", name="Approve seller evidence")
            ).to_have_count(0)
        page.goto(portal + "/#lab", wait_until="networkidle")
        page.locator("#lab-seller").select_option("demo-kabir")
        if use_llm:
            page.locator('#start-session input[name="use_llm"]').check()
        page.get_by_role("button", name="Start rehearsal").click()
        expect(page.locator("#live-start")).to_be_visible()
        assert page.locator("#voice-language option").count() == 11
        assert page.locator("#voice-speaker option").count() == 6
        page.locator("#voice-consent").check()
        page.locator("#live-start").click()
        try:
            page.wait_for_function(
                "() => document.querySelector('#live-status').textContent.includes('Live ·')",
                timeout=25000,
            )
        except Exception:
            debug = {
                "status": page.locator("#live-status").inner_text(),
                "mic_stage": page.evaluate("window.__micStage"),
                "mock_installed": page.evaluate(
                    "navigator.mediaDevices.getUserMedia.toString().includes('encoded')"
                ),
                "errors": errors,
                "events": [e.get("event") for e in events],
                "toasts": page.locator("#toast-region").inner_text()
                if page.locator("#toast-region").count()
                else page.locator("body").inner_text()[-2000:],
            }
            (output / (engine + "-debug.json")).write_text(
                json.dumps(debug, indent=2)
            )
            page.screenshot(
                path=str(output / (engine + "-debug.png")), full_page=True
            )
            raise
        page.evaluate("() => window.__startSyntheticMicrophone()")
        # Assert the actual saved adaptation, allowing provider punctuation/wording differences.
        expect(page.locator(".lab-layout aside")).to_contain_text(
            "Confusion", timeout=60000
        )
        assert any(
            event.get("event") == "turn"
            and event["result"]["signal"] == "confusion"
            for event in events
        ), "No genuine confusion turn was persisted"
        assert page.locator("#conversation .message-user").count() >= 1
        (
            output
            / f"{engine}-{width}-{base.split('//')[1].split('/')[0]}-events.json"
        ).write_text(
            json.dumps(
                [
                    {
                        key: value
                        for key, value in event.items()
                        if key not in {"audio", "result"}
                    }
                    | (
                        {"signal": event["result"]["signal"]}
                        if event.get("event") == "turn"
                        else {}
                    )
                    for event in events
                ],
                indent=2,
            )
        )
        if use_llm:
            assert any(
                event.get("event") == "turn"
                and event["result"]["method"] == "llm"
                for event in events
            ), "The requested model reply fell back"
        reply_generation = next(
            event["generation"]
            for event in reversed(events)
            if event.get("event") == "turn"
        )
        page.wait_for_function(
            "expected => Number(document.querySelector('#live-metrics').getAttribute('data-audio-generation')) === expected",
            arg=reply_generation,
            timeout=45000,
        )
        page.locator("#live-interrupt").click()
        page.locator("#live-stop").click()
        expect(page.locator("#live-status")).to_contain_text(
            "Microphone released"
        )
        expect(page.locator("#live-start")).to_be_enabled()
        expect(page.locator("#live-stop")).to_be_disabled()
        expect(page.locator("#live-interrupt")).to_be_disabled()
        expect(page.locator("#turn-text")).to_be_enabled()
        assert page.evaluate(
            "window.__micTracks.length>0&&window.__micTracks.every(track=>track.readyState==='ended')"
        ), "Microphone tracks were not released"
        assert any(
            event.get("event") == "transcript.partial" for event in events
        )
        assert any(
            event.get("event") == "transcript.final" for event in events
        )
        assert any(event.get("event") == "interruption" for event in events)
        assert any(event.get("event") == "audio" for event in events)
        assert not [
            event for event in events if event.get("event") == "error"
        ], [event for event in events if event.get("event") == "error"]
        page.screenshot(
            path=str(
                output
                / f"{engine}-{width}-{base.split('//')[1].split('/')[0]}-live.png"
            ),
            full_page=True,
        )
        page.get_by_role("button", name="Finish & evaluate").click()
        expect(page.locator(".outcome")).to_be_visible()
        assert not errors, errors
        modules = page.evaluate(
            "performance.getEntriesByType('resource').filter(x=>/\\/(live|speech|pcm-worklet)\\.js/.test(x.name)).map(x=>x.name)"
        )
        version = page.locator("meta[name=app-version]").get_attribute(
            "content"
        )
        assert len(modules) >= 2 and all(
            "v=" + version in url for url in modules
        ), modules
        worklet_urls = page.evaluate("window.__workletUrls")
        assert worklet_urls and all(
            "/pcm-worklet.js?v=" + version in url for url in worklet_urls
        ), worklet_urls
        result = {
            "status": "passed",
            "engine": engine,
            "width": width,
            "base": base,
            "version": version,
            "public_clips": 6,
            "batch_status": "not_run_this_case" if live_only else "completed",
            "reply_method": "llm" if use_llm else "live_rules",
            "live_events": sorted(set(e.get("event") for e in events)),
            "partial_count": sum(
                e.get("event") == "transcript.partial" for e in events
            ),
            "audio_chunks": sum(e.get("event") == "audio" for e in events),
            "microphone_released": True,
            "latencies": [
                e["response_audio_ms"]
                for e in events
                if e.get("event") == "latency"
            ],
            "hardware_acoustics_tested": False,
            "worklet_urls": worklet_urls,
            "errors": errors,
        }
        context.close()
        browser.close()
        return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-only", action="store_true")
    parser.add_argument("--llm", action="store_true")
    parser.add_argument(
        "--portal-path", default="", help="UI route; APIs stay at the base URL"
    )
    parser.add_argument(
        "--engine", choices=["chromium", "webkit"], default="chromium"
    )
    parser.add_argument(
        "--url",
        default="https://sellergenome.lehana.in",
    )
    parser.add_argument("--width", type=int, default=1440)
    parser.add_argument("--height", type=int, default=900)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path("/opt/secrets/env_files/seller-persona-v3-qa.env"),
    )
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    config = dict(
        line.split("=", 1)
        for line in args.env_file.read_text().splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    )
    receipt = run(
        args.url,
        args.width,
        args.height,
        args.engine,
        config["PM_PASSWORD"].strip("\"'"),
        args.out,
        args.fixture,
        args.live_only,
        args.llm,
        args.portal_path,
    )
    (
        args.out
        / f"{args.engine}-{args.width}-{args.url.split('//')[1].split('/')[0]}.json"
    ).write_text(json.dumps(receipt, indent=2))
    print(json.dumps(receipt))
