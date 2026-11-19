# Hunch Home: sharing daily counts

Privacy notice, 19 November 2026.

Nothing leaves your computer unless you choose to share it to make Hunch better. This notice is about that one
choice: **Share daily counts**, under Hunch Home's **Configure** in Home Assistant. It is off unless you turn it
on, and Hunch Home works just the same without it.

## Who we are

Hunch Home is made by xevall (xevall.com). xevall decides what is collected and why, and is the one to write
to about it: privacy@xevall.com.

## What is sent

Once a day, for the day before (in UTC), and only if Hunch Home recorded at least one sentence that day, it
sends one message of counts:

- `schema`: the message's format number.
- `day`: the date counted.
- `install_id`: an install number, 32 random letters and digits made on your machine for the counts alone. It
  is not linked to your name, email address or Home Assistant account.
- `plugin_version` and `model_versions`: the version numbers of Hunch Home and of the Hunch model it used.
- `sentences`: how many sentences it handled.
- `by_action_and_kind`: how many of each kind of command, such as "turn on / Lights".
- `by_outcome`: how many were carried out, handed back to Home Assistant, ended in an error, or were
  answered by Home Assistant first.
- `handed_back_by_reason`: why sentences were handed back, from a fixed list such as "no match".
- `errors`: how many times Home Assistant reported an error.
- `undone`: how many commands were put back the other way within 30 seconds.
- `scores`: Hunch's confidence, in rough bands such as "0.8 to 0.9".

Every word in the message comes from a fixed list in Hunch Home's code; anything else is counted as "other".
Download diagnostics for Hunch Home to see exactly what would be sent for yesterday. One day's message:

```json
{"schema": 1, "day": "2026-11-18", "install_id": "3f9c2a7be41d4c0a9e6b15d2c8a07f31",
 "plugin_version": "0.1.0", "model_versions": {"1.1": 13}, "sentences": 17,
 "by_action_and_kind": {"not for me / none": 1, "set temperature / Climate": 2, "turn off / Lights": 3,
                        "turn on / Lights": 7},
 "by_outcome": {"carried out": 11, "handed back": 2, "answered by Home Assistant first": 4},
 "handed_back_by_reason": {"no match": 1, "not for me": 1}, "errors": 0,
 "undone": {"no": 8, "yes": 1, "not known": 2, "not applicable": 6},
 "scores": {"0.9 and above": 6, "0.8 to 0.9": 5, "0.6 to 0.7": 1, "below 0.5": 1}}
```

## What is never sent

Anything you say or type, the names of your devices, entities or rooms, and anything else from your home.

The message goes to xevall.com, which passes it straight on to our server. Neither stores the internet
address a message comes from, or anything else about the connection. Vercel, which hosts xevall.com, and
Supabase, which runs the server, keep their own short-term technical logs of requests, which include that
address. The message names its sender as "HunchHome" and says nothing about your Home Assistant or your
machine.

## Why

To see whether Hunch is used, and where it gets things wrong, so we can make it better. We may publish
figures added up across many homes. We never publish one home's counts.

## Lawful basis

Your consent, under the UK GDPR. You give it by turning the option on, and you can withdraw it at any time.
Hunch Home's free licence never depends on it.

## Where it is kept, and who sees it

In a database we run on Supabase, in Switzerland (Zurich). Supabase is our processor: it stores the counts for us
and for nothing else. Vercel is our processor too: it passes each message on and keeps none of it. Only xevall
can read the counts. We never sell them or share them with anyone else.

## How long

24 months from the day counted. Then they are deleted automatically.

## Withdrawing, and your rights

Turn **Share daily counts** off. Nothing more is sent, and Hunch Home asks our server to delete every count
sent from your home. If it cannot reach the server, it tries again every hour until it can.

If you remove Hunch Home while sharing is on, it asks once for the same deletion. If that fails, the install
reference is gone with it, so note the reference first (from the diagnostics download, under "sharing").

You can also ask us for a copy of your counts, or to delete them, at privacy@xevall.com. We can only find
them by your install reference: it is in the diagnostics download, under "sharing", "install_reference".
Please quote it. The download never shows the install number itself, so it is safe to post in public.

You can complain to the Information Commissioner's Office (ico.org.uk) if you are unhappy with how we handle
your data.

## Your local record is separate

Hunch Home also keeps a record of each sentence and what it did, in your own Home Assistant, so you can see
what went wrong. Hunch Home never sends that record anywhere, whether sharing is on or off. It is part of your
Home Assistant's own data, so your Home Assistant backups hold it too, wherever you keep them. You can switch
it off or clear it under **Configure**.
