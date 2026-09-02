import { useEffect, useState } from 'react';
import { Board } from './Board';

// The review example from the mockup: 20...Qd7?? just played, 21.Nxf6+ wins the queen.
const FEN = '5rk1/p2qbppp/1p2pn2/3N4/4P3/4B3/PP2QPPP/3R2K1 w - - 1 21';

type Motif = { kind: string; mover: string; attacker: string; targets: string[]; with_check: boolean; safe: boolean };

export default function App() {
  const [motifs, setMotifs] = useState<Motif[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch('/api/motifs', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ fen: FEN, san: 'Nxf6+' }),
    })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`API ${r.status}`))))
      .then((data: { motifs: Motif[] }) => setMotifs(data.motifs))
      .catch((e: Error) => setError(e.message));
  }, []);

  return (
    <main style={{ display: 'flex', gap: 24, padding: 24 }}>
      <Board
        fen={FEN}
        orientation="black"
        shapes={[
          { orig: 'd5', dest: 'f6', brush: 'red' },
          { orig: 'd1', dest: 'd7', brush: 'blue' },
        ]}
      />
      <section style={{ flex: 1 }}>
        <h1 style={{ fontFamily: 'var(--font-display)', fontSize: 24, margin: '0 0 12px' }}>체스 튜터</h1>
        <p style={{ fontFamily: 'var(--font-mono)', fontWeight: 600 }}>20… Qd7 ?? → 21. Nxf6+</p>
        {error && <p style={{ color: 'var(--bad)' }}>API 연결 실패: {error} (uvicorn이 8000번에서 실행 중인지 확인)</p>}
        {motifs && (
          <ul>
            {motifs.map((m, i) => (
              <li key={i} style={{ fontFamily: 'var(--font-mono)' }}>
                {m.kind}: {m.attacker} → {m.targets.join(', ')}
                {m.with_check ? ' (+check)' : ''}
                {m.safe ? '' : ' (capturable)'}
              </li>
            ))}
          </ul>
        )}
      </section>
    </main>
  );
}
