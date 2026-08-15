# OptiKinesis — Project Description

> **Hands-free computer access for people who cannot rely on their hands.**
>
> Webcam-based cursor control + blink selection + voice shortcuts — with an optional IMU MotionGlass wearable for precision.

---

🏆 **Top 40 — Samsung Solve for Tomorrow 2026 · AI in Healthcare Track**

**Team Revolutionaries** — Govind Upadhyay · Aditya Kumar · Himal Garg

---

## 1. What is OptiKinesis?

OptiKinesis is a **software-only assistive technology system** that transforms any ordinary laptop webcam into a complete hands-free computer control system for people with motor neuron diseases and other conditions that affect hand use.

**No special hardware. No expensive sensors. No surgical implants.** Just the webcam that is already built into every modern laptop.

The core thesis is simple:

> **Digital independence should not require expensive specialist hardware.**
> Start with software. Upgrade only when a user needs higher precision.

---

## 2. The Problem We're Solving

### The digital isolation of motor disability

Conditions like **ALS (Amyotrophic Lateral Sclerosis)**, **Parkinson's disease**, **spinal cord injuries**, **stroke**, **cerebral palsy**, and **muscular dystrophy** progressively destroy the motor neurons or muscles that let people use their hands. The result: people with perfectly functioning minds become unable to interact with the digital world.

In 2026, losing the use of your hands means:

| What you lose | Why it matters |
|---|---|
| Typing messages | Cannot communicate with family, friends, or colleagues independently |
| Browsing the internet | Cannot research your own condition, read news, or learn |
| Sending emails | Cannot correspond with doctors, employers, or institutions |
| Calling for help | Cannot trigger emergency services without a caregiver present |
| Working | Cannot earn a livelihood even when the mind is fully capable |
| Self-expression | Cannot express thoughts, needs, or emotions without physical help |

### The scale

- **2.5 Billion+** people globally need at least one assistive product today (WHO)
- **1 Billion** people who need assistive technology are denied access
- **5.4 Million** Indians recorded with movement disability (Census 2011)
- Access to assistive products can be as low as **3%** in some countries

### Why existing solutions fail

The assistive technology market is split into two extremes — prohibitively expensive devices that work well, and cheap devices that barely work at all.

| Existing Solution | Cost | Critical Limitation |
|---|---|---|
| **Tobii Eye Trackers** | $3,000 – $15,000+ | Proprietary hardware required; unaffordable for most families |
| **Brain-Computer Interfaces** | $10,000+ or research-only | Requires surgery or lab equipment; not consumer-ready |
| **Sip-and-Puff switches** | $200 – $800 | Painfully slow single-switch scanning; no cursor freedom |
| **SmartNav head tracker** | $400 – $600 | Requires infrared reflective dots; no integrated click mechanism |

**The gap:** There is no affordable, software-only solution that provides full computer access — cursor control, clicking, typing, voice commands, and emergency communication — using hardware people already own.

OptiKinesis fills that gap.

---

## 3. How OptiKinesis Works

### Three layers of interaction

OptiKinesis provides three complementary interaction methods designed to cover the full spectrum of motor ability:

### Layer 1: Head-Pose Cursor Control

**The user moves their head to move the mouse cursor.**

The system uses **MediaPipe's Face Landmarker** to detect and track 478 points on the user's face at 30 frames per second through the webcam. From five key anchor points (left edge, right edge, top, bottom, and front of the face), OptiKinesis computes a 3D "gaze ray" — a direction vector pointing where the user's head is facing.

This vector is decomposed into:
- **Yaw** (horizontal angle) → mapped to screen X coordinate
- **Pitch** (vertical angle) → mapped to screen Y coordinate

**Sensitivity tuning:** A head turn of only ±25° covers the entire screen width. This means users with severely limited neck mobility can still reach every corner of their display.

**Smoothing:** A 12-frame rolling average combined with Exponential Moving Average (EMA) filtering eliminates the natural jitter of webcam tracking, producing cursor movement that feels deliberate and controlled rather than shaky.

### Layer 2: Blink-to-Click

**The user blinks deliberately to click.**

The system monitors the **Eye Aspect Ratio (EAR)** — a geometric ratio computed from four landmarks around the left eye. When the eye closes (EAR drops below a configurable threshold), a blink is detected.

**Critical accuracy feature:** Since closing your eyes disrupts face tracking, OptiKinesis captures the cursor position *before* the blink begins and freezes the cursor during the blink. The click lands exactly where the user was looking — not where the face tracker drifts during the blink.

**Auto-keyboard detection:** When the cursor hovers over a text input field (detected via the Windows I-beam cursor shape), the virtual keyboard opens automatically, just like on a smartphone. This eliminates the need to manually navigate to the keyboard button.

### Layer 3: Voice Commands

**The user speaks to execute complex tasks in a single command.**

Even with perfect cursor control, some tasks (like searching Google or sending an email) require dozens of clicks and keystrokes. For a user who clicks by blinking, this is exhausting. Voice commands collapse multi-step workflows into a single spoken sentence:

- *"Search how to cook pasta on Google"* → opens Google with the search results
- *"Open YouTube and search relaxing music"* → launches YouTube with the query
- *"Send an email to [address], subject [subject], content [message]"* → drafts and sends
- *"Set a reminder in 30 minutes"* → triggers a system reminder
- *"Set an alarm for 7 AM"* → schedules an alarm

Destructive or sensitive actions (sending emails, making calls) require explicit user confirmation before execution.

---

## 4. The Desktop Overlay

All interaction happens through a **floating desktop overlay** built with PyQt5 that sits on top of all other applications. The overlay is designed to:

- **Never steal focus** from the user's active application (uses `WindowDoesNotAcceptFocus` and `Tool` window flags)
- **Never block** the user's view of their work (translucent, compact, positioned at screen edges)
- **Be fully operable by blink-clicking** (all buttons are large, high-contrast, and gaze-friendly)

### Modules

| Module | Icon | Description |
|---|---|---|
| **Keyboard** | ⌨ | Full QWERTY virtual keyboard with Shift, Caps Lock, numbers, and special characters. Characters are typed directly into the focused application's text field via `pyautogui` — no intermediate text box. Three action buttons (Search Google, Search YouTube, Type External) allow quick dispatch of typed text. |
| **Camera** | 📷 | Live preview of the webcam feed so the user or caregiver can verify face tracking is working correctly. A **mini camera feed** also stays visible at all times in the bottom-right corner of the screen. |
| **Emergency** | 🚨 | One-tap SOS actions: send a WhatsApp message to a saved emergency contact, trigger an automated phone call via Twilio, or direct-dial emergency services (100 for police, 112 for ambulance in India). |
| **Control** | ⚙ | Adjustable settings for cursor speed, blink sensitivity, and lock delay. Caregivers can tune the system to match each individual user's motor ability. Includes calibration, mouse lock/unlock, and overlay toggle. |
| **Voice** | 🎤 | Activate voice recognition to speak commands instead of typing and clicking. |

### Control Bar States

The floating control bar has two states:
1. **Idle:** A compact "OPTIKINESIS" pill at the top center of the screen
2. **Expanded:** When the cursor hovers over the pill, it expands to reveal all five module buttons plus a status indicator

---

## 5. Technical Architecture

### Pipeline

```
Webcam (30 FPS)
    │
    ▼
MediaPipe Face Landmarker (478 points)
    │
    ├── HEAD-POSE ESTIMATION              ├── BLINK DETECTION
    │   5 landmarks → 3D gaze ray →       │   4 eye landmarks → EAR →
    │   Yaw/Pitch → Screen X,Y            │   Threshold check → Click
    │                                      │
    ▼                                      ▼
Mouse Mover Thread                   Gaze Click Bridge
(pyautogui every 10ms)               (Qt hit-test → route click)
    │                                      │
    └──────────────┬───────────────────────┘
                   ▼
         PyQt5 Desktop Overlay
         (Keyboard, Camera, Emergency, Control, Voice)
```

### File Structure

| File | Purpose |
|---|---|
| `main.py` | Entry point. Camera capture loop, MediaPipe face tracking, head-pose computation, blink detection, mouse control thread, Flask API server, and PyQt5 overlay launch. |
| `desktop_overlay.py` | All PyQt5 overlay UI code: `CursorOverlay` (gaze ring), `FloatingControlBar`, `KeyboardOverlayPanel`, `CameraOverlayPanel`, `EmergencyOverlayPanel`, `ControlOverlayPanel`, `MiniCameraOverlay`, `GazeClickBridge`, and `BlinkDebugOverlay`. |
| `voice_commands.py` | Voice command NLP engine: intent parsing via regex patterns, action execution (Google search, YouTube, email, alarm, reminder), confirmation workflow, and logging. |
| `fatigue_monitor.py` | Rolling-window blink-rate analyzer that detects excessive blinking (fatigue) and can reduce sensitivity to prevent accidental clicks. |
| `eye_blink.py` | Standalone blink detection utilities and EAR computation. |
| `preview_overlay.py` | Developer tool: launches the overlay UI with live-reload for rapid design iteration without needing the full camera loop. |
| `face_landmarker.task` | Pre-trained MediaPipe face landmark model (3.7 MB). |
| `OVERLAY_UI_MAP.md` | Developer reference: maps specific line numbers in `desktop_overlay.py` to the UI elements they control. |

### Technology Stack

| Layer | Technology | Why |
|---|---|---|
| Face tracking | MediaPipe Face Landmarker (Tasks API) | Lightweight, runs on CPU, no GPU required |
| Computer vision | OpenCV | Industry standard for webcam capture and frame processing |
| Desktop overlay | PyQt5 | Cross-platform frameless translucent windows with full widget toolkit |
| Mouse control | PyAutoGUI | Cross-platform cursor movement, clicking, and keyboard simulation |
| Voice NLP | Regex-based intent parser | Lightweight, no internet required for parsing |
| Emergency comms | Twilio API + WhatsApp Web | Reliable delivery for SOS alerts |
| Backend API | Flask | REST endpoints for settings, actions, and inter-process communication |
| Cursor detection | ctypes (Windows API) | Detects I-beam cursor for auto-keyboard trigger |

### System Requirements

| Requirement | Specification |
|---|---|
| **Hardware** | Any computer with a webcam (built-in laptop cameras work perfectly) |
| **Operating System** | Windows (primary), macOS/Linux via PyQt5 |
| **Python** | 3.8+ |
| **Additional hardware cost** | ₹0 — uses existing webcam |
| **Internet** | Optional (needed only for voice commands, emergency calls, and WhatsApp) |

---

## 6. What Makes OptiKinesis Different

### vs. Commercial Alternatives

| Capability | Tobii Eye Tracker | SmartNav | Sip-and-Puff | OptiKinesis |
|---|---|---|---|---|
| Hardware cost | $3,000–$15,000 | $400–$600 | $200–$800 | **₹0** |
| Additional hardware | Proprietary sensor | IR dot | Mouth tube | **None** |
| Click method | Dwell-time | External switch | Puff | **Natural blink** |
| Virtual keyboard | Third-party | Third-party | Third-party | **Built-in** |
| Auto-keyboard on text fields | No | No | No | **Yes** |
| Voice commands | No | No | No | **Yes** |
| Emergency SOS | No | No | No | **Yes** |
| Hardware upgrade path | Locked in | Locked in | None | **MotionGlass** |
| Open source | No | No | No | **Yes** |
| Deployable in developing countries | Rarely | Rarely | Limited | **Yes** |

### Key differentiators

1. **Software-first + hardware-upgrade path:** Start free with a webcam. Add MotionGlass only if needed.
2. **All-in-one accessibility ecosystem:** Cursor, keyboard, voice, emergency — one integrated system.
3. **Zero additional hardware cost:** The core system requires nothing beyond a laptop.
4. **Emergency support + caregiver controls:** SOS alerts and tunable sensitivity aren't afterthoughts.
5. **Auto-keyboard detection:** Text fields trigger the keyboard automatically, reducing navigation effort.
6. **Non-focus-stealing overlay:** The entire UI is designed to never take focus away from the user's active application.

---

## 7. Upcoming: MotionGlass

**A lightweight IMU-enabled wearable** that integrates with OptiKinesis to deliver smoother, faster, and more precise hands-free control by combining inertial sensing with computer vision.

| Specification | Detail |
|---|---|
| **Sensor** | 6-axis IMU (accelerometer + gyroscope) |
| **Connectivity** | Bluetooth Low Energy (BLE) |
| **Form factor** | Glasses, clip-on, or headband module |
| **Integration** | Supplements webcam tracking with sub-degree motion sensing |

MotionGlass is designed as an **upgrade, not a replacement.** Users who find the webcam-only solution sufficient never need to buy it. Users who need higher precision or faster response can add it seamlessly.

---

## 8. Impact

### Who benefits

**Patients:** ALS/MND, Parkinson's, spinal cord injury, stroke, cerebral palsy, muscular dystrophy, locked-in syndrome — anyone who has lost hand function but retains head movement and the ability to blink.

**Caregivers:** Reduced burnout, peace of mind from independent emergency alerts, minimal setup and maintenance.

**Healthcare institutions:** Low-cost assistive technology that can be deployed across patient rooms without per-device licensing.

**NGOs and government programs:** A solution that can be distributed at scale in developing countries with zero hardware cost.

### The moral argument

Stephen Hawking used a $7,000 infrared cheek-sensor system custom-built by Intel. He had access to the best technology because of his fame and institutional support.

**The 2.5 billion other people who need assistive technology do not have Intel building them a custom solution.**

OptiKinesis exists because access to digital communication should not be gated by wealth, geography, or celebrity. A laptop webcam and an open-source Python script should be enough.

---

## 9. Market Opportunity

| Segment | Value |
|---|---|
| **Total Addressable Market (TAM)** | $2.8B — Global AAC Device Market in 2026, growing to $5–6B by 2035 at 9–11% CAGR |
| **Serviceable Available Market (SAM)** | 75M+ people globally require assistive technology for daily activities (WHO) |
| **Serviceable Obtainable Market (SOM)** | 1–2M — initial focus on ALS & Parkinson's patients via hospitals, rehab centres, NGOs, and direct-to-consumer |

### Business tiers

| Tier | Target | Offering |
|---|---|---|
| **Access** (software) | Individual patients | One-time purchase: webcam control, blink click, keyboard, voice, SOS |
| **MotionGlass** (hardware) | Precision users | IMU wearable kit with Bluetooth setup and fusion mode |
| **Care** (service) | Ongoing support | Quarterly calibration, health checks, priority support |
| **Institutional** (bundle) | NGOs / Hospitals | Multi-user profiles, training, fleet monitoring, replacement workflow |

---

## 10. Recognition

🏆 **Samsung Solve for Tomorrow 2026** — Selected as a **Top 40 project** in the **AI in Healthcare** track, out of thousands of entries from across India.

---

## 11. Getting Started

### Prerequisites
- Python 3.8+
- A computer with a webcam

### Installation
```bash
git clone <repository-url>
cd ALS_Testing_phase
pip install -r requirements.txt
```

### Run
```bash
python main.py
```

The system launches a floating overlay on your desktop:
1. **Move your head** to control the cursor
2. **Blink** to click
3. **Hover over the control bar** at the top to access Keyboard, Camera, Emergency, Control, and Voice modules
4. **Look at a text field and blink** — the keyboard opens automatically
5. **Blink on keyboard keys** — characters appear directly in the active text field

---

## 12. Team

| Member | Role | Key Contributions |
|---|---|---|
| **Himal Garg** | Product & AI/CV Lead | Problem framing, MediaPipe/OpenCV head-pose control, blink-to-click logic, system architecture, prototype development, pitch narrative |
| **Aditya Kumar** | Hardware & IoT Lead | MotionGlass concept, IMU + BLE architecture, sensor selection, wearable prototyping plan, device diagnostics, hardware feasibility |
| **Govind Upadhyay** | Research, UX & Impact Lead | NGO insight documentation, user journey, accessibility workflow design, testing plan, caregiver feedback, pilot-partner outreach |

---

<div align="center">

*Digital independence should not be limited by whether a patient can afford specialist assistive hardware.*

**OptiKinesis — Team Revolutionaries**

</div>
