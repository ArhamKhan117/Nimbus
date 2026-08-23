"use client";

/**
 * The approval queue.
 *
 * Approving emails a licence key to a real person, so the confirmation is not a `confirm()` dialog — it
 * shows the email address and the reference in the button's own row, because the mistake to prevent here
 * is approving the wrong row, not clicking by accident.
 *
 * The token lives in a `useState` and nowhere else. See the page's comment for why.
 */
import { useState } from "react";

type Payment = {
  id: string;
  email: string;
  name: string | null;
  method: string;
  reference: string;
  note: string;
  status: string;
  createdAt: string;
};

export function AdminPayments() {
  const [token, setToken] = useState("");
  const [payments, setPayments] = useState<Payment[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  const [issued, setIssued] = useState<Record<string, string>>({});

  const headers = () => ({ authorization: `Bearer ${token}`, "content-type": "application/json" });

  async function load() {
    setBusy(true);
    setProblem("");
    try {
      const response = await fetch("/api/admin/payments", { headers: headers() });
      if (response.status === 401) throw new Error("That token was not accepted.");
      if (!response.ok) throw new Error("Could not load the queue.");
      const body = (await response.json()) as { payments: Payment[] };
      setPayments(body.payments);
    } catch (error) {
      setProblem(error instanceof Error ? error.message : "Could not load the queue.");
    } finally {
      setBusy(false);
    }
  }

  async function decide(id: string, action: "approve" | "reject") {
    setBusy(true);
    setProblem("");
    try {
      const response = await fetch("/api/admin/payments", {
        method: "POST",
        headers: headers(),
        body: JSON.stringify({ id, action, months: 1 }),
      });
      const body = (await response.json()) as { key?: string; error?: string };
      if (!response.ok) throw new Error(body.error ?? "That did not work.");
      if (body.key) setIssued((current) => ({ ...current, [id]: body.key as string }));
      setPayments((current) => (current ?? []).filter((payment) => payment.id !== id));
    } catch (error) {
      setProblem(error instanceof Error ? error.message : "That did not work.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <div className="surface grain relative overflow-hidden p-6">
        <label className="block text-[14px] font-semibold text-ink-2" htmlFor="token">
          Admin token
        </label>
        <div className="mt-2 flex flex-wrap gap-3">
          <input
            id="token"
            type="password"
            className="field flex-1 font-mono"
            value={token}
            onChange={(event) => setToken(event.target.value)}
            placeholder="ADMIN_TOKEN"
            autoComplete="off"
          />
          <button type="button" className="btn" onClick={load} disabled={busy || !token}>
            {busy ? "Working\u2026" : "Load queue"}
          </button>
        </div>
        {problem ? <p className="mt-3 text-[14px] text-danger">{problem}</p> : null}
      </div>

      {Object.entries(issued).map(([id, key]) => (
        <p
          key={id}
          className="mt-3 rounded-[8px] border border-accent/40 bg-accent/10 px-4 py-3 font-mono text-[14px]"
        >
          Issued {key} &mdash; emailed to the tester.
        </p>
      ))}

      {payments?.length === 0 ? (
        <p className="mt-6 text-ink-2">Nothing waiting. Everyone who has paid has their key.</p>
      ) : null}

      <ul className="mt-4 space-y-3">
        {(payments ?? []).map((payment) => (
          <li key={payment.id} className="surface grain relative overflow-hidden p-5">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <span className="font-semibold">{payment.email}</span>
              <span className="text-[13px] text-ink-3">
                {payment.createdAt.slice(0, 16).replace("T", " ")} UTC
              </span>
            </div>
            <dl className="mt-3 grid gap-x-6 gap-y-1 text-[15px] sm:grid-cols-2">
              <div className="flex justify-between gap-3 border-b border-line pb-1">
                <dt className="text-ink-2">Method</dt>
                <dd className="capitalize">{payment.method.replace("-", " ")}</dd>
              </div>
              <div className="flex justify-between gap-3 border-b border-line pb-1">
                <dt className="text-ink-2">Reference</dt>
                <dd className="select-all font-mono">{payment.reference || "\u2014"}</dd>
              </div>
            </dl>
            {payment.note ? <p className="mt-3 text-[14px] text-ink-2">{payment.note}</p> : null}

            <div className="mt-4 flex flex-wrap gap-3">
              <button
                type="button"
                className="btn btn-primary"
                disabled={busy}
                onClick={() => decide(payment.id, "approve")}
              >
                Approve &amp; email key
              </button>
              <button
                type="button"
                className="btn"
                disabled={busy}
                onClick={() => decide(payment.id, "reject")}
              >
                Reject
              </button>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
