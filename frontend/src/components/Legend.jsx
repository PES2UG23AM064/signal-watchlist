import Drawer from "./ui/Drawer.jsx";

// Every label this app puts on a number, explained in plain words. Tooltips carry the same text on a
// laptop, but a phone has no hover — so the meaning has to be reachable somewhere you can tap.

function Item({ chip, children }) {
  return (
    <div className="py-3 border-b border-line last:border-0">
      <div className="mb-1.5">{chip}</div>
      <p className="text-xs text-ink-2 leading-relaxed">{children}</p>
    </div>
  );
}

function Group({ title, children }) {
  return (
    <section>
      <h3 className="eyebrow mb-1">{title}</h3>
      {children}
    </section>
  );
}

export default function Legend({ open, onClose }) {
  return (
    <Drawer
      open={open}
      onClose={onClose}
      title="What the labels mean"
      subtitle="Every number here says where it came from and how old it is."
    >
      <div className="space-y-6">
        <Group title="How fresh the price is">
          <Item
            chip={
              <span className="chip chip-fresh">
                <span className="w-1.5 h-1.5 rounded-full bg-fresh" />
                fresh <span className="num text-ink-4 font-normal">12s ago</span>
              </span>
            }
          >
            The price is under 20 seconds old. The number is how long ago the source stamped it — not when we
            drew the screen. Fresh says how <em>old</em> it is, not where it's from — that's the source label below.
          </Item>
          <Item
            chip={
              <span className="flex gap-1.5">
                <span className="chip chip-delayed">
                  <span className="w-1.5 h-1.5 rounded-full bg-delayed" /> delayed
                </span>
                <span className="chip chip-stale">
                  <span className="w-1.5 h-1.5 rounded-full bg-stale" /> stale
                </span>
              </span>
            }
          >
            We haven't received a new price for a while. Rather than show you an old number as if it were
            fresh, we show you exactly how old it is.
          </Item>
        </Group>

        <Group title="Where the price came from">
          <Item chip={<span className="chip chip-sim">simulated</span>}>
            These prices are a replay built from real market data, not a live exchange feed. We label it so
            that moving prices next to "Market closed" never reads as us making things up.
          </Item>
          <Item chip={<span className="chip chip-stale">⚠ 3 refused</span>}>
            We rejected 3 bad price ticks in the last five minutes — impossible prices, jumps beyond the
            exchange's circuit limit, or timestamps from the future. They were stored for audit and never
            shown to you. The price above them was never affected.
          </Item>
          <Item chip={<span className="chip chip-stale">disputed 5%</span>}>
            A second, independent data source disagrees with the price we're showing by 5%. We show you the
            disagreement instead of quietly picking a winner. The price on the card is still the primary
            source.
          </Item>
        </Group>

        <Group title="Why something showed up">
          <Item chip={<span className="chip chip-alone">moving alone</span>}>
            This stock is behaving very differently from the stocks it normally moves with. That usually means
            the news is about this company, not the whole market — so we push it to the top.
          </Item>
          <Item chip={<span className="chip chip-cohort">co-movement pack</span>}>
            These stocks normally rise and fall together, so we fold them into a single card. It's the sector
            moving, not one company doing something. Nothing is hidden — open the card to see every symbol.
          </Item>
        </Group>

        <Group title="The two buttons">
          <Item chip={<span className="chip chip-brand">Seen</span>}>
            Sets a new starting point. From now on, "what changed" is measured from this price and this
            moment.
          </Item>
          <Item chip={<span className="chip bg-surface2 text-ink-2">Snooze</span>}>
            Keeps the stock on your list but stops it counting as needing attention for an hour. It stays
            visible, just muted — we never silently drop something.
          </Item>
        </Group>

        <p className="text-2xs text-ink-4 leading-relaxed pt-1">
          Signal describes what already happened. It never predicts prices and never tells you what to buy.
          The evidence for that is in the Insights tab.
        </p>
      </div>
    </Drawer>
  );
}
