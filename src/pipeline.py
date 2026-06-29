"""
Kids-cartoon generation orchestrator.

Runs the agent pipeline as an ordered, RESUMABLE state machine:

  PLAN → STORY → DIALOGUE → SCENE → PROMPT → PERSIST_STORY
       → IMAGES → VOICE → SUBTITLES → THUMBNAIL → RENDER → UPLOAD → ARCHIVE

Every step's status + artifact is persisted to data/runs/<uid>.json AND the
PipelineRun DB row, so a crashed run can `resume()` and any single failed step
can be re-run via `regenerate_step()`. Heavy media modules (PIL/MoviePy/edge-tts
/google-genai) are imported lazily INSIDE their steps, so this module loads and
the whole CONTENT half runs even when the media stack isn't installed.

Business logic lives here, never in Streamlit — the dashboard calls
`Orchestrator` / the thin service wrappers, nothing more.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from src.agents import (
    AgentContext, DialogueAgent, PlannerAgent, PromptAgent, SceneAgent, StoryAgent,
)
from src.config import config
from src.content_policy import PolicyViolation, check_story_package
from src.llm import LLM, default_llm
from src.story_schema import StoryPackage
from src.utils import get_logger, sanitise_filename

log = get_logger("pipeline")

# Ordered pipeline. CONTENT steps need only the light stack; MEDIA steps need
# the heavy stack and are skipped in dry runs.
CONTENT_STEPS = ["PLAN", "STORY", "DIALOGUE", "SCENE", "PROMPT", "PERSIST_STORY"]
MEDIA_STEPS = ["IMAGES", "VOICE", "SUBTITLES", "THUMBNAIL", "RENDER", "UPLOAD", "ARCHIVE"]
ALL_STEPS = CONTENT_STEPS + MEDIA_STEPS


@dataclass
class StepState:
    name: str
    status: str = "pending"        # pending|running|done|failed|skipped
    artifact: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    ts: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "status": self.status,
                "artifact": self.artifact, "error": self.error, "ts": self.ts}

    @classmethod
    def from_dict(cls, d: dict) -> "StepState":
        return cls(name=d["name"], status=d.get("status", "pending"),
                   artifact=d.get("artifact") or {}, error=d.get("error"),
                   ts=d.get("ts", ""))


@dataclass
class RunResult:
    run_uid: str
    status: str
    steps: list[StepState]
    package: StoryPackage | None = None
    video_path: str | None = None
    youtube_url: str | None = None
    cost_usd: float = 0.0
    error: str | None = None


class Orchestrator:
    def __init__(self, llm: LLM | None = None, session=None):
        self.llm = llm or default_llm()
        from src.database import (
            CharacterRepo, PipelineRunRepo, StoryRepo, VideoRepo, AnalyticsRepo,
            get_session, init_db,
        )
        init_db()
        self.session = session or get_session()
        self.characters = CharacterRepo(self.session)
        self.stories = StoryRepo(self.session)
        self.runs = PipelineRunRepo(self.session)
        self.videos = VideoRepo(self.session)
        self.analytics = AnalyticsRepo(self.session)

    # ── public API ───────────────────────────────────────────────────────────
    def run(
        self, *,
        topic: str | None = None,
        category: str | None = None,
        language: str | None = None,
        scene_count: int | None = None,
        target_seconds: int | None = None,
        dry: bool = False,
        skip_upload: bool = False,
        upload_mode: str = "manual",
        resume_uid: str | None = None,
        only_steps: list[str] | None = None,
        progress: Callable[[str, str], None] | None = None,
    ) -> RunResult:
        """Generate ONE kids Short. `dry` stops after PERSIST_STORY (no media).
        `upload_mode`: 'manual' uploads with no time gate; 'auto' respects the
        24h automatic-upload gate (and only auto uploads advance that timer).
        `resume_uid` continues a prior run; `only_steps` runs just those steps."""
        ctx, state, uid = self._bootstrap(
            topic, category, language, scene_count, target_seconds, resume_uid
        )
        self._progress = progress
        self._upload_mode = upload_mode
        steps_to_run = only_steps or ALL_STEPS
        last_media = "PERSIST_STORY" if dry else "ARCHIVE"
        cutoff = ALL_STEPS.index(last_media)

        handlers = self._handlers(skip_upload=skip_upload)
        result_status = "done"
        for i, name in enumerate(ALL_STEPS):
            if i > cutoff:
                break
            if name not in steps_to_run:
                continue
            st = state[name]
            if st.status == "done" and self._artifact_valid(name, st):
                self.log.skip(name)
                continue
            try:
                self._set(state, uid, name, "running")
                artifact = handlers[name](ctx, state)
                st.artifact = artifact or {}
                self._set(state, uid, name, "done")
            except PolicyViolation as pv:
                # Content rule broken → re-roll the content half ONCE.
                st.error = str(pv)
                self._set(state, uid, name, "failed")
                log.warning("Policy violation at %s: %s", name, pv)
                result_status = "failed"
                break
            except Exception as e:  # noqa: BLE001 — record + stop, keep run resumable
                st.error = f"{type(e).__name__}: {e}"
                self._set(state, uid, name, "failed")
                log.error("Step %s failed: %s", name, e, exc_info=True)
                result_status = "failed"
                break

        if not ctx.package.narration_script:
            ctx.package.rebuild_script()
        self._persist_package(uid, ctx.package)
        return self._finalize(uid, state, result_status, ctx)

    def resume(self, run_uid: str, **kw) -> RunResult:
        return self.run(resume_uid=run_uid, **kw)

    def regenerate_step(self, run_uid: str, step: str, **kw) -> RunResult:
        """Reset `step` and everything after it, then continue from there."""
        step = step.upper()
        if step not in ALL_STEPS:
            raise ValueError(f"Unknown step {step!r}")
        state = self._load_state(run_uid)
        from_idx = ALL_STEPS.index(step)
        for name in ALL_STEPS[from_idx:]:
            state[name] = StepState(name=name, status="pending")
        self._save_state(run_uid, state, current_step=step, status="running")
        return self.run(resume_uid=run_uid, **kw)

    # ── bootstrap / state ──────────────────────────────────────────────────--
    @property
    def log(self):
        return _StepLog(log)

    def _bootstrap(self, topic, category, language, scene_count, target_seconds, resume_uid):
        roster = [self._char_dict(c) for c in self.characters.get_active()]
        language = (language or config.KIDS_LANGUAGE or "hi").lower()
        scene_count = scene_count or config.KIDS_SCENE_COUNT
        target_seconds = target_seconds or config.KIDS_TARGET_SECONDS

        if resume_uid:
            state = self._load_state(resume_uid)
            pkg = self._load_package(resume_uid) or StoryPackage(language=language)
            uid = resume_uid
            log.info("Resuming run %s", uid)
        else:
            base = f"{sanitise_filename(topic or category or 'kids')}_{int(time.time())}"
            uid, n = base, 1
            while self.runs.by_uid(uid):   # guarantee uniqueness for same-second runs
                n += 1
                uid = f"{base}_{n}"
            state = {name: StepState(name=name) for name in ALL_STEPS}
            pkg = StoryPackage(topic=topic or "", category=category or "", language=language)
            self.runs.create(run_uid=uid, mode="kids")
            self._save_state(uid, state, status="running")
            log.info("Starting run %s (lang=%s, scenes=%d, ~%ds)",
                     uid, language, scene_count, target_seconds)

        ctx = AgentContext(
            package=pkg, roster=roster, llm=self.llm, language=language,
            scene_count=scene_count, target_seconds=target_seconds,
            max_characters=config.KIDS_MAX_CHARACTERS,
            topic=topic, category=category,
        )
        if resume_uid:
            # Rebuild the working notes (slug, file paths, ids) from the
            # already-persisted step artifacts so media steps resume correctly
            # even in a fresh process.
            self._restore_notes(state, ctx)
        return ctx, state, uid

    def _restore_notes(self, state, ctx) -> None:
        def art(name: str) -> dict:
            return state[name].artifact if state[name].status == "done" else {}

        ps = art("PERSIST_STORY")
        ctx.notes.setdefault("slug", ps.get("slug")) if ps.get("slug") else None
        if ps.get("story_id"):
            ctx.notes["story_id"] = ps["story_id"]
        if art("IMAGES").get("path"):
            ctx.notes["image_paths"] = art("IMAGES")["path"]
        if art("VOICE").get("path"):
            ctx.notes["audio_path"] = art("VOICE")["path"]
        if art("THUMBNAIL").get("path"):
            ctx.notes["thumbnail_path"] = art("THUMBNAIL")["path"]
        if art("RENDER").get("path"):
            ctx.notes["video_path"] = art("RENDER")["path"]

    def _char_dict(self, c) -> dict:
        return {
            "id": c.id, "name": c.name, "species": c.species,
            "personality": c.personality, "appearance_prompt": c.appearance_prompt,
            "negative_prompt": c.negative_prompt or "", "seed": c.seed or 0,
            "reference_image": c.reference_image or "",
            "voice_engine": c.voice_engine, "voice_id": c.voice_id,
            "voice_rate": c.voice_rate, "voice_pitch": c.voice_pitch,
        }

    def _state_path(self, uid: str) -> Path:
        return config.RUNS_DIR / f"{uid}.json"

    def _package_path(self, uid: str) -> Path:
        return config.RUNS_DIR / f"{uid}.package.json"

    def _save_state(self, uid, state, *, current_step="", status="running") -> None:
        steps = [state[n].to_dict() for n in ALL_STEPS]
        self._state_path(uid).write_text(
            json.dumps({"uid": uid, "status": status, "current_step": current_step,
                        "steps": steps, "updated": datetime.utcnow().isoformat()},
                       ensure_ascii=False, indent=2), encoding="utf-8")
        run = self.runs.by_uid(uid)
        if run:
            self.runs.save_steps(run.id, steps, current_step=current_step, status=status)

    def _load_state(self, uid) -> dict[str, StepState]:
        p = self._state_path(uid)
        state = {name: StepState(name=name) for name in ALL_STEPS}
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                for sd in data.get("steps", []):
                    if sd["name"] in state:
                        state[sd["name"]] = StepState.from_dict(sd)
            except Exception as e:
                log.warning("Could not load run state %s: %s", uid, e)
        return state

    def _persist_package(self, uid, pkg: StoryPackage) -> None:
        try:
            self._package_path(uid).write_text(pkg.to_json(), encoding="utf-8")
        except Exception as e:
            log.debug("package persist failed: %s", e)

    def _load_package(self, uid) -> StoryPackage | None:
        p = self._package_path(uid)
        if p.exists():
            try:
                return StoryPackage.from_json(p.read_text(encoding="utf-8"))
            except Exception:
                return None
        return None

    def _set(self, state, uid, name, status) -> None:
        state[name].status = status
        state[name].ts = datetime.utcnow().isoformat()
        self._save_state(uid, state, current_step=name, status="running")
        if self._progress:
            try:
                self._progress(name, status)
            except Exception:
                pass

    def _artifact_valid(self, name, st: StepState) -> bool:
        """For media steps the artifact is a file path; re-use only if it still
        exists. Content steps are always considered valid once done."""
        path = st.artifact.get("path")
        if name in ("IMAGES", "VOICE", "THUMBNAIL", "RENDER") and path:
            if isinstance(path, list):
                return all(Path(p).exists() for p in path)
            return Path(path).exists()
        return True

    # ── step handlers ──────────────────────────────────────────────────────--
    def _handlers(self, *, skip_upload: bool) -> dict[str, Callable]:
        return {
            "PLAN": self._step_plan,
            "STORY": self._step_story,
            "DIALOGUE": self._step_dialogue,
            "SCENE": self._step_scene,
            "PROMPT": self._step_prompt,
            "PERSIST_STORY": self._step_persist_story,
            "IMAGES": self._step_images,
            "VOICE": self._step_voice,
            "SUBTITLES": self._step_subtitles,
            "THUMBNAIL": self._step_thumbnail,
            "RENDER": self._step_render,
            "UPLOAD": (self._step_skip_upload if skip_upload else self._step_upload),
            "ARCHIVE": self._step_archive,
        }

    def _step_plan(self, ctx, state):
        PlannerAgent().run(ctx)
        return {"category": ctx.package.category, "characters": ctx.package.characters,
                "title": ctx.package.title}

    def _step_story(self, ctx, state):
        StoryAgent().run(ctx)
        return {"hook": ctx.package.hook, "words": len(ctx.package.script.split())}

    def _step_dialogue(self, ctx, state):
        DialogueAgent().run(ctx)
        return {"dialogue_by_character": ctx.notes.get("dialogue_by_character", {})}

    def _step_scene(self, ctx, state):
        SceneAgent().run(ctx)
        return {"scenes": len(ctx.package.scenes)}

    def _step_prompt(self, ctx, state):
        PromptAgent().run(ctx)
        return {"prompts": len(ctx.package.image_prompts)}

    def _step_persist_story(self, ctx, state):
        check_story_package(ctx.package)
        pkg = ctx.package
        story = self.stories.create(
            title=pkg.title, category=pkg.category, language=pkg.language,
            logline=pkg.logline, moral=pkg.moral,
            characters=json.dumps(pkg.characters, ensure_ascii=False),
            package_json=pkg.to_json(), status="ready",
        )
        ctx.notes["story_id"] = story.id
        ctx.notes["slug"] = f"{sanitise_filename(pkg.title or 'story')}_{int(time.time())}"
        return {"story_id": story.id, "slug": ctx.notes["slug"]}

    # ── media steps (lazy heavy imports) ───────────────────────────────────--
    def _slug(self, ctx) -> str:
        return ctx.notes.get("slug") or f"{sanitise_filename(ctx.package.title or 'story')}_{int(time.time())}"

    def _step_images(self, ctx, state):
        from src.image_backends import render_scene_images
        slug = self._slug(ctx)
        scenes = ctx.package.scenes
        # Prefer consistency data persisted on the scenes (survives resume);
        # fall back to in-process notes.
        seeds = [sc.seed for sc in scenes] or ctx.notes.get("scene_seeds")
        negs = [sc.negative_prompt for sc in scenes] or ctx.notes.get("scene_negatives")
        paths = render_scene_images(
            ctx.package.image_prompts, slug, seeds=seeds, negatives=negs)
        ctx.notes["image_paths"] = [str(p) for p in paths]
        return {"path": [str(p) for p in paths], "count": len(paths)}

    def _step_voice(self, ctx, state):
        from src.voiceover import VoiceoverGenerator
        slug = self._slug(ctx)
        audio_path, segments = VoiceoverGenerator().generate(
            ctx.package.script, slug, language=ctx.package.language)
        ctx.notes["audio_path"] = str(audio_path)
        ctx.notes["word_segments"] = segments
        return {"path": str(audio_path), "words": len(segments)}

    def _word_segments(self, ctx) -> list[dict]:
        """word timings from this run, or the on-disk TTS timing cache (so a
        cross-process resume at SUBTITLES/RENDER still has them)."""
        segs = ctx.notes.get("word_segments")
        if not segs:
            from src.voiceover import VoiceoverGenerator
            segs = VoiceoverGenerator()._load_timing(self._slug(ctx))
            ctx.notes["word_segments"] = segs
        return segs or []

    def _step_subtitles(self, ctx, state):
        from src.captions import build_caption_cues
        segs = self._word_segments(ctx)
        cues = build_caption_cues(segs, max_words=config.CAPTION_MAX_WORDS)
        return {"cues": len(cues), "words": len(segs)}

    def _step_thumbnail(self, ctx, state):
        from src.thumbnail_generator import ThumbnailGenerator
        slug = self._slug(ctx)
        imgs = ctx.notes.get("image_paths") or []
        base = Path(imgs[0]) if imgs else None
        path = ThumbnailGenerator().generate(
            ctx.package.thumbnail_text or ctx.package.title,
            ctx.package.title, slug, theme_idx=0, base_image=base)
        ctx.notes["thumbnail_path"] = str(path)
        return {"path": str(path)}

    def _step_render(self, ctx, state):
        from src.video_generator import VideoGenerator
        slug = self._slug(ctx)
        imgs = [Path(p) for p in (ctx.notes.get("image_paths") or [])]
        # Resolve uploaded character art → puppet/cutout mode (free, no GPU).
        # If no character has art, this stays empty and the renderer uses the
        # existing kinetic style.
        char_images: dict[str, str] = {}
        for c in ctx.roster:
            ref = c.get("reference_image")
            if not ref:
                continue
            p = Path(ref)
            if not p.is_absolute():
                p = config.BASE_DIR / ref
            if p.exists():
                char_images[c["name"]] = str(p)
        if char_images:
            log.info("Puppet mode: art for %s", list(char_images))
        path = VideoGenerator().generate(
            title=ctx.package.title, script=ctx.package.script,
            subtitle_segments=self._word_segments(ctx),
            image_paths=imgs, audio_path=Path(ctx.notes["audio_path"]),
            slug=slug, hook=ctx.package.hook, cta=ctx.package.cta,
            language=ctx.package.language,
            scenes=ctx.package.scenes, character_images=char_images or None,
        )
        ctx.notes["video_path"] = str(path)
        self._record_video(ctx)
        return {"path": str(path)}

    def _step_upload(self, ctx, state):
        from src.upload_service import upload_video
        vid = ctx.notes.get("video_db_id")
        if not vid:                       # RENDER normally records it; be safe
            self._record_video(ctx)
            vid = ctx.notes.get("video_db_id")
        result = upload_video(vid, mode=getattr(self, "_upload_mode", "manual"),
                              session=self.session)
        if result.get("skipped"):
            # auto gate not open yet — NOT a failure; we just wait for the timer
            log.info("UPLOAD skipped — %s", result.get("reason"))
            return {"skipped": True, "reason": result.get("reason")}
        ctx.notes["youtube_url"] = result.get("url")
        self.analytics.increment("videos_uploaded")
        return {"url": result.get("url"), "id": result.get("id")}

    def _step_skip_upload(self, ctx, state):
        return {"skipped": True}

    def _step_archive(self, ctx, state):
        from src.utils import safe_delete  # noqa
        # Light archival: record the run summary; temp cleanup is conservative.
        summary = {
            "title": ctx.package.title, "video": ctx.notes.get("video_path"),
            "youtube": ctx.notes.get("youtube_url"), "cost_usd": round(ctx.cost_usd, 5),
        }
        (config.ARCHIVE_DIR / f"{self._slug(ctx)}.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return summary

    def _record_video(self, ctx) -> None:
        if ctx.notes.get("video_db_id"):
            return
        pkg = ctx.package
        v = self.videos.create(
            title=pkg.youtube_title or pkg.title,
            description=pkg.youtube_description,
            script=pkg.script, hashtags=json.dumps(pkg.hashtags, ensure_ascii=False),
            thumbnail_text=pkg.thumbnail_text, kind="kids",
            story_id=ctx.notes.get("story_id"),
            audio_path=ctx.notes.get("audio_path"),
            video_path=ctx.notes.get("video_path"),
            thumbnail_path=ctx.notes.get("thumbnail_path"),
            status="generated",
            gemini_input_tokens=ctx.input_tokens,
            gemini_output_tokens=ctx.output_tokens,
            estimated_cost_usd=round(ctx.cost_usd, 6),
        )
        ctx.notes["video_db_id"] = v.id
        self.analytics.increment("videos_generated")
        self.analytics.increment("total_cost_usd", round(ctx.cost_usd, 6))

    # ── finalize ─────────────────────────────────────────────────────────────
    def _finalize(self, uid, state, status, ctx) -> RunResult:
        steps = [state[n] for n in ALL_STEPS]
        done = [s for s in steps if s.status == "done"]
        if status != "failed":
            status = "done" if len(done) >= len(CONTENT_STEPS) else "partial"
        self._save_state(uid, state, current_step="", status=status)
        run = self.runs.by_uid(uid)
        if run:
            self.runs.save_steps(run.id, [s.to_dict() for s in steps],
                                 status=status, story_id=ctx.notes.get("story_id"),
                                 video_id=ctx.notes.get("video_db_id"),
                                 error_message=next((s.error for s in steps if s.error), None))
        return RunResult(
            run_uid=uid, status=status, steps=steps, package=ctx.package,
            video_path=ctx.notes.get("video_path"),
            youtube_url=ctx.notes.get("youtube_url"),
            cost_usd=round(ctx.cost_usd, 6),
            error=next((s.error for s in steps if s.error), None),
        )


class _StepLog:
    def __init__(self, base):
        self._b = base

    def skip(self, name):
        self._b.info("· %s already done — skipping", name)
