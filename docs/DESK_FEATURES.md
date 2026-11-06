# Desk features: This week and the desk on your phone

Two additions to the desk (`frontend/src/app/(app)/desk`), both built only from what Vakya already stores. Neither adds a
backend route, a provider or a key. They follow the existing design: off-white page, Gambarino headings, Inter text,
`#e6e6e6` borders and the violet `#6c62ed` accent, used only on numbers and the current item.

| Feature | Where | What it is for |
|---|---|---|
| **This week** | `/desk/week`, in the sidebar under Today, in the command palette, and as a tab on phones | The owner sees in ten seconds what the front desk did over the last seven days |
| **The desk on your phone** | Every desk page on a phone, plus Settings, "This phone" | The owner runs the desk from their phone between customers and gets a ping for each new booking |

## 1. This week (`/desk/week`)

### What the page shows

1. **One sentence.** "In the last seven days Vakya answered 42 calls, 20 of them while you were closed, and booked 17
   appointments." Below it, each count is set against the seven days before ("Calls up from 30 the week before") when
   there were any, plus the minutes on the phone from the stored call durations.
2. **When people call.** A grid of the seven days (oldest left, today last) by the hour of the day. Each cell holds that
   hour's call count, and the violet fill gets stronger with more calls. Hours when every schedule was closed are hatched.
   The hatch stays under a filled cell, so violet on a hatch means a call that came while the business was closed. The
   busiest hour is named above the grid. Rows run from 8 am to 9 pm and widen to fit any call or opening hour outside that.
3. **How the calls ended.** One bar per outcome, in a fixed order: booked, answered, needs approval, needs a person,
   declined, couldn't answer, ended early. The labels are the same as on the Calls page.
4. **Languages callers used.** The share of calls by the language most replies were in. Calls with no detected language
   are listed last.
5. **Calls worth a look.** Up to six of the week's calls that ended in "couldn't answer", "ended early", "needs a person"
   or "needs approval", newest first. Each one opens its transcript on the Calls page.

### Where every number comes from

Nothing is estimated: each number is a count of stored rows, worked out in the browser by
`frontend/src/lib/weekReport.ts` (pure functions, tested in `lib/__tests__/weekReport.test.ts`).

| Number | Source and rule |
|---|---|
| Calls answered | `GET /api/calls?days=14&limit=200`. Counts phone and web calls only. Practice calls, calls where nobody spoke (`empty`) and WhatsApp threads are left out |
| The seven days | Today and the six days before, as calendar days in the desk's time zone (`timezone` from `/api/calls`, Asia/Kolkata by default), never the browser's |
| While you were closed | A counted call whose time falls outside every time-slot schedule's weekly hours, or on a schedule's closed date. Without any opening hours the clause is left out, and the page says how to get it. A hotel's nights schedule has no opening hours, so it does not count |
| Appointments booked | `GET /api/bookings`: live bookings whose `created_at` falls in the seven days. Staff additions (`source: staff`) and practice calls are left out |
| Minutes on the phone | The sum of `duration_s` of the counted calls, rounded. Shown only when at least one call has a duration |
| The week before | The same rules applied to the seven days before |

If the calls list is cut at its 200-row limit (`truncated`), the page says the counts are a floor.

### States

- **Loading.** Skeletons in the page's shape.
- **Error.** The calls request failed: an error with Retry.
- **Empty.** No counted call in seven days: a short explanation and Try a call.

## 2. The desk on your phone

### On a phone screen (below the `md` breakpoint, 768 px)

- **Tab bar** (`components/phone/MobileTabBar.tsx`): Today, Bookings, Calls and This week sit at the bottom, one thumb
  away. The current page is marked with a short violet line and the lit icon. Every other page stays in the menu at the
  top. The bar clears the home indicator of an installed iPhone app (`env(safe-area-inset-bottom)`), and the page leaves
  room for it.
- **Today**: the four counts sit two by two, and the speech-cost tiles are left out (Settings still shows them), so the
  day's appointments are reached sooner.

### Installing the desk (Add to Home screen)

`frontend/src/app/manifest.ts` serves `/manifest.webmanifest`:

| Field | Value |
|---|---|
| Name | "Vakya desk", short name "Vakya" |
| Opens on | `/desk` (Today), full screen (`standalone`), portrait |
| Icons | `public/brand/vakya-app-icon-192.png`, `-512.png` and `.svg` (the 192 px icon is the 512 px one scaled down) |
| Long-press shortcuts | Bookings, Calls, This week |

The desk pages set `viewport-fit=cover` and a white theme colour (`app/(app)/layout.tsx`), so the installed app runs edge
to edge with white bars that match its top bar. The public site is unchanged.

Settings, **This phone**, shows the state on the device in use:

- **Installed:** the desk is open as the app.
- **Install the desk:** the browser offers a one-tap install (Chrome and Edge fire `beforeinstallprompt`; the desk keeps
  it in `lib/deskAlerts.ts`).
- **From the browser menu:** with the steps for Android Chrome (menu, Add to Home screen) and iPhone Safari (Share, Add
  to Home Screen).

It also shows a QR code of the desk's own address to open it on a phone.

### Booking alerts

`components/phone/BookingAlerts.tsx` is mounted once in the desk shell (`components/LayoutWrapper.tsx`) and renders
nothing:

1. Every 30 seconds, also while the tab is in the background, it reads `GET /api/bookings`. This is the same request Today
   makes, so the browser does not send it twice.
2. It compares each booking's `created_at` with the newest booking this device has already announced, stored in
   `localStorage` as `vakya-alerts-seen`. The first time on a device it only sets that mark, so old bookings are never
   announced.
3. For each new live booking (not staff-added, not from a practice call) it shows a toast in the desk: "New booking:
   Sunita Kulkarni", "Thu 12 Nov, 7 pm · Dr. Joshi", with an Open button to the booking.
4. When alerts are on for the device and the desk is not in front, it also shows a system notification with the same
   text. Tapping it opens the booking. More than three new bookings at once become one summary ("5 new bookings").

Alerts are turned on per device in Settings, This phone. The browser asks for permission once. The choice is kept as
`vakya-alerts` in `localStorage`, and there is a "Send a test alert" button. Notifications go through
`public/sw.js`, a service worker that only shows the alert and opens the booking on a tap. It caches nothing and handles
no requests, so the desk never shows stale data. Android needs a service worker to show any notification.

### What it does not do (on purpose)

- **No push server.** An alert arrives only while the desk is open, in a tab (even in the background) or as the app on
  the phone. A phone that has put the app to sleep, or a desk that was closed, gets nothing at the time. The next time it
  opens, it announces what was booked meanwhile. Waking a closed app needs Web Push: a push service, keys and a sender on
  the backend. That would be a new backend piece, left for later.
- **No offline copy.** The desk always shows what the server has now.
- **iPhone.** Safari shows notifications only for a desk added to the home screen (iOS 16.4 and later), so on an
  iPhone, install the desk first.

### Using it from a real phone

- The phone must reach both the web app and the API. In local development the web app calls the API at
  `NEXT_PUBLIC_API_URL`, which defaults to `http://localhost:8020`, and on a phone `localhost` is the phone itself. Run
  the desk behind an https address the phone can open, the same kind of tunnel `PUBLIC_BASE_URL` uses for the phone
  line, and set `NEXT_PUBLIC_API_URL` to an API address the phone can reach. Settings, This phone, warns when either
  address is localhost.
- Installing and notifications need https, except on `localhost` itself.

## Files

| File | Role |
|---|---|
| `frontend/src/lib/weekReport.ts` | The week's numbers (pure) |
| `frontend/src/components/week/WeekView.tsx` | The page's sections, loading, error and empty states |
| `frontend/src/app/(app)/desk/week/page.tsx`, `layout.tsx` | The route and its title, "This week · Vakya" |
| `frontend/src/lib/deskAlerts.ts` | Which bookings are new, the alert text, permission, the service worker, the install offer |
| `frontend/src/components/phone/BookingAlerts.tsx` | Watches for new bookings in the desk shell |
| `frontend/src/components/phone/MobileTabBar.tsx` | The phone tab bar |
| `frontend/src/components/phone/PhoneDeskSettings.tsx` | Settings, This phone |
| `frontend/src/app/manifest.ts`, `public/sw.js`, `public/brand/vakya-app-icon-192.png` | Installing and notifications |
| `frontend/src/components/icons/NavIcons.tsx` | `WeekIcon`: seven voice bars, the busiest one lit |
| `frontend/src/lib/formatDate.ts` | `wallClock`, the time-zone parts of an instant, now exported for the week |

Tests: `lib/__tests__/weekReport.test.ts` (11), `lib/__tests__/deskAlerts.test.ts` (6),
`components/__tests__/MobileTabBar.test.tsx` (1), and the sidebar test now lists This week.

## Checking it by hand

1. `./vakya start`, then open http://localhost:3010/desk/week. With no calls in seven days you see the empty state. Make
   a few calls from Try a call; practice calls are left out, so use a web call link or the phone line.
2. Narrow the window below 768 px: the tab bar appears and Today's counts sit two by two.
3. Settings, This phone, Turn on alerts. Allow, then Send a test alert. Book something on a call from another tab and
   switch away: an alert arrives within 30 seconds.
