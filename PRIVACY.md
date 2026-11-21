# Hunch Home: privacy notice

Privacy notice for the Hunch Home app, version 0.2.0.

Hunch Home runs on your own machine, and the model that understands your sentences is inside it. Nothing leaves
your machine by Hunch Home's own doing, apart from two things you choose: the daily counts, which are off unless
you turn them on, and the one download that listening with no wake word needs. This notice says what Hunch Home
does with what you type and say, where a sentence can go, and everything about the daily counts.

## Who we are

Hunch Home is made by xevall (xevall.com). xevall decides what is collected and why, and is the one to write
to about it: privacy@xevall.com.

## What stays on your machine

**Sentences from Assist.** Home Assistant gives Hunch Home the words of each sentence that its own sentences did
not answer first, with the language and the voice device it came from. Hunch Home gets words, never sound: if you
speak, your assistant's speech-to-text has already turned your voice into words, on your machine or in the cloud,
as you set your assistant up.

**The record.** Hunch Home keeps each sentence and what became of it, so you can see it on the app's page: when;
the sentence; Hunch's answer and score; the devices it acted on, or why it passed the sentence on; the reply; how
long it took; and whether it was undone. It keeps the latest 1,000 sentences, none older than 30 days, in the
app's own folder on your machine. It never sends the record anywhere. Your Home Assistant backups hold it too,
wherever you keep them. Switch it off, or clear it, under **Settings** on the app's page. Switched off, nothing
new is kept, but what was kept before stays, on your machine, in backups and in the downloads, until you clear
it. Under **Your data** you can download it whole, or as a version made for a bug report, which leaves out the sentences and the names of
devices, rooms and agents.

**What you teach it.** Sentences you teach Hunch, and the answers you chose, are kept in the app's own folder on
your machine, and never sent anywhere. You can remove each one on the page.

**Listening with no wake word** (experimental, off unless you switch it on). Sound from the microphone is turned
into words on your machine, by a speech model that runs inside the app. Nothing it hears is sent anywhere. Words
it does not act on are not kept: no record, nothing on the page, nothing in the log. A command it carries out is
kept in the record like any other, marked as heard by the microphone. It never passes anything on to another
agent. When you switch it off, the microphone is closed at once and nothing heard before is acted on.

**The log.** At its usual level, the app's log holds no sentences and no device names.

## Where a sentence can go

**Sentences passed on to your other agent.** What is not a home command (a question, a chat, something Hunch has
no way to do, or a sentence it is not sure is a home command), Hunch passes to the agent you chose under **Pass on
to** on the app's page: by default the agent your assistant had before. A home command stays with Hunch, which
carries it out or says why not, and it stays on your machine. A sentence passed on goes through your own Home
Assistant, as if you had asked that agent yourself, together with the earlier sentences passed to it in the same
conversation. If that agent is Home Assistant's own, the sentence stays on your machine. If it is a language
model you connected in Home Assistant, such as OpenAI's or Anthropic's, the sentence is sent to that company under
its terms and its privacy notice, not xevall's. xevall never sees it. Hunch Home holds no key for any such
service and calls none itself.

**The speech model, once.** The first time you switch on listening with no wake word, and only after you confirm
it on the page, Hunch Home downloads its speech model (Parakeet TDT 0.6B v2, by NVIDIA) from GitHub, from the
sherpa-onnx project's releases. GitHub sees that request, with your machine's internet address, as it sees any
download, under GitHub's terms. Nothing about you or your home is sent with it.

**Installing and updating.** Home Assistant downloads the app's image from GitHub's container registry when you
install or update it, as it does for any app.

## Sharing daily counts (your choice, off unless you turn it on)

**Share daily counts** is a switch under **Settings** on the app's page. It is off unless you turn it on, and
Hunch Home works the same without it.

### What is sent

Once a day, for the day before (in UTC), and only if Hunch Home recorded at least one sentence that day, it sends
one message of counts. The counts are made from the record, so with **Keep a record** off nothing new is counted.
On the day you switch it off, what was kept earlier that day is still counted, unless you clear the record.
Sentences heard by listening with no wake word are never counted: only sentences marked as having come through
Assist are. The app's page shows, under **Your
data**, exactly what would be sent for yesterday, whether sharing is on or off.

- `schema`: the message's format number.
- `day`: the date counted.
- `install_id`: an install number, 32 random letters and digits made on your machine for the counts alone. It
  is not linked to your name, email address or Home Assistant account.
- `plugin_version` and `model_versions`: the version numbers of Hunch Home and of the Hunch model it used.
- `sentences`: how many sentences it handled.
- `by_action_and_kind`: how many of each kind of command, such as "turn on / Lights".
- `by_outcome`: how many were carried out, passed on to your other agent or not acted on at all (the message
  calls both "handed back"), or ended in an error.
- `handed_back_by_reason`: why sentences were passed on or not acted on, from a fixed list such as "no match".
- `errors`: how many times Home Assistant reported an error.
- `undone`: how many commands were put back the other way soon after.
- `scores`: Hunch's confidence, in rough bands such as "0.8 to 0.9".

Every word in the message comes from a fixed list in Hunch Home's code; anything else is counted as "other". One
day's message:

```json
{"schema": 1, "day": "2026-11-18", "install_id": "3f9c2a7be41d4c0a9e6b15d2c8a07f31",
 "plugin_version": "0.2.0", "model_versions": {"1.1": 17}, "sentences": 17,
 "by_action_and_kind": {"not for me / none": 3, "set temperature / Climate": 2, "turn off / Lights": 4,
                        "turn on / Lights": 8},
 "by_outcome": {"carried out": 11, "handed back": 6},
 "handed_back_by_reason": {"declined": 1, "no match": 2, "not for me": 3}, "errors": 0,
 "undone": {"no": 8, "yes": 1, "not known": 2, "not applicable": 6},
 "scores": {"0.9 and above": 8, "0.8 to 0.9": 5, "0.6 to 0.7": 2, "below 0.5": 2}}
```

### What is never sent

Anything you say or type, anything you taught it, the names of your devices, entities or rooms, and anything
else from your home.

The message goes to xevall.com, which passes it straight on to our server. Neither stores the internet address
a message comes from, or anything else about the connection. Vercel, which hosts xevall.com, and Supabase, which
runs the server, keep their own short-term technical logs of requests, which include that address. The message
names its sender as "HunchHome" and says nothing about your Home Assistant or your machine.

### Why

To see whether Hunch is used, and where it gets things wrong, so we can make it better. We may publish figures
added up across many homes. We never publish one home's counts.

### Lawful basis

Your consent, under the UK GDPR. You give it by turning the switch on, and you can withdraw it at any time.
Hunch Home's free licence never depends on it.

### Where it is kept, and who sees it

In a database we run on Supabase, in Switzerland (Zurich). Supabase is our processor: it stores the counts for us
and for nothing else. Vercel is our processor too: it passes each message on and keeps none of it. Only xevall
can read the counts. We never sell them or share them with anyone else.

### How long

24 months from the day counted. Then they are deleted automatically.

### Withdrawing, and your rights

Turn **Share daily counts** off. Nothing more is sent, and Hunch Home asks our server, straight away, to delete
every count sent from your home. If it cannot reach the server, it tries again every hour until it can, and sends
nothing more meanwhile, even if you turn sharing on again. The page says, under **Your data**, whether the
deletion has gone through.

Uninstalling Hunch Home deletes the app's own folder, and with it your install number, which is what finds and
deletes your counts. If you uninstall while sharing is on, the app cannot ask for that deletion, and you can no
longer quote the number. So before you uninstall, either turn sharing off and leave the app running for a minute
(that asks for the deletion; **Your data** on the page says when it has gone through), or note your install
reference from **Your data** to quote when you write to us.

You can also ask us for a copy of your counts, or to delete them, at privacy@xevall.com. Quote your install
reference, shown under **Your data** on the app's page: it is made from your install number, which is how we find
your counts, and cannot be turned back into it. The number itself is in the file `record.json` in the app's own
folder, under `install_id`, and in any backup of the app. It is what deletes your counts, so keep it to yourself
and send it only to us.

**If the record file is damaged.** If the app ever finds its record file damaged, it sets the file aside under
another name in the same folder and starts a new record with a new install number. The first is
`record.json.unreadable`; any later one has the day and a number added, such as
`record.json.unreadable-2026-11-20-2`, and none is ever written over. Counts sent after that carry the new number.
The old number, and with it the counts sent before, can still be found in the file it kept: quote it to us to have
those deleted too. The page lists each such file under **Your data**, newest first, with its number's reference
when it can still be read. The app keeps at most ten of these files and never deletes one: if ten are kept
already, it leaves the damaged file as it is, saves nothing over it, and says so at the top of its page until you
remove some.

## Complaints

You can complain to the Information Commissioner's Office (ico.org.uk) if you are unhappy with how we handle
your data.
