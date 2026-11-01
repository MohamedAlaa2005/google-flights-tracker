// Hopper Live Scraper & Supabase Bridge Extension
(function() {
  console.log('[Hopper Scraper Extension] Injected into Hopper.');

  let hasAutoSynced = false;

  function extractCurrentInfo() {
    const text = document.body ? document.body.innerText : '';
    const lowestMatch = text.match(/Current lowest price:\s*\$([\d,]+)/i)
      || text.match(/\$([\d,]+)\s*One-way,\s*per traveler/i);
    const price = lowestMatch ? lowestMatch[1] : null;

    let rating = 'Great price';
    if (text.includes('Wait for a better price') || text.includes('Wait')) {
      rating = 'Wait';
    } else if (text.includes('Fair price')) {
      rating = 'Fair price';
    }

    let airline = '';
    const cardImg = document.querySelector('img[src*="airline_icons"]');
    if (cardImg) {
      const cardParent = cardImg.closest('div');
      if (cardParent) {
        const pTag = cardParent.querySelector('p');
        if (pTag && pTag.innerText) {
          airline = pTag.innerText.trim();
        }
      }
    }

    const hasFlightList = !!document.querySelector('[data-testid="air-flight-list"]') 
      || !!document.querySelector('div[data-index]')
      || (price && airline);

    return { price, rating, airline: airline || 'Operating Airline', hasFlightList };
  }

  function createOverlay() {
    if (document.getElementById('hopper-scraper-overlay')) return;

    const overlay = document.createElement('div');
    overlay.id = 'hopper-scraper-overlay';
    overlay.innerHTML = `
      <div class="hso-header">
        <span class="hso-title">⚡ Hopper Scraper</span>
        <span style="font-size:11px; color:#94a3b8;">Live Supabase Bridge</span>
      </div>
      <div class="hso-info">
        <div class="hso-price" id="hso-price-val">Detecting flights...</div>
        <div id="hso-details" style="font-size:12px; color:#cbd5e1;">Scanning Hopper live DOM...</div>
      </div>
      <div class="hso-actions">
        <button class="hso-sync-btn" id="hso-sync-btn">
          <span>🚀 Sync to Supabase</span>
        </button>
        <button class="hso-next-btn" id="hso-next-btn" title="Go to next date (+1 day)">
          +1 Day ❯
        </button>
      </div>
      <div class="hso-toast" id="hso-toast"></div>
    `;

    document.body.appendChild(overlay);

    const updateInfo = async () => {
      const info = extractCurrentInfo();
      const priceVal = document.getElementById('hso-price-val');
      const detailsVal = document.getElementById('hso-details');

      if (info.price) {
        if (priceVal) priceVal.innerText = `$${info.price} USD`;
        if (detailsVal) detailsVal.innerText = `${info.airline} • ${info.rating}`;

        // Auto-sync if not done yet
        const urlParams = new URLSearchParams(window.location.search);
        const autoSyncEnabled = urlParams.get('autoSync') === 'true' || window.__AUTO_SYNC_ENABLED__;

        if (autoSyncEnabled && !hasAutoSynced && info.hasFlightList) {
          hasAutoSynced = true;
          await triggerSync(false);
        }
      }
    };

    setInterval(updateInfo, 1000);

    async function triggerSync(isManual = true) {
      const toast = document.getElementById('hso-toast');
      const btn = document.getElementById('hso-sync-btn');
      if (btn) {
        btn.disabled = true;
        btn.innerText = '⏳ Syncing...';
      }
      if (toast) toast.style.display = 'none';

      try {
        const payload = {
          html: document.documentElement.outerHTML,
          url: window.location.href,
        };

        const res = await fetch('http://localhost:3000/api/scrape/hopper-html', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });

        const data = await res.json();
        if (data.success && toast) {
          toast.style.color = '#4ade80';
          toast.innerText = `✅ Synced to Supabase: ${data.overallLowest?.airline || 'Flight'} at $${data.overallLowest?.current_lowest_price}!`;
          toast.style.display = 'block';

          const urlParams = new URLSearchParams(window.location.search);
          if (urlParams.get('autoNext') === 'true') {
            setTimeout(() => {
              stepNextDate();
            }, 3000);
          }
        } else if (toast) {
          toast.style.color = '#f87171';
          toast.innerText = `❌ Error: ${data.error || 'Failed to sync'}`;
          toast.style.display = 'block';
        }
      } catch (err) {
        if (toast) {
          toast.style.color = '#f87171';
          toast.innerText = `❌ Server offline? (${err.message})`;
          toast.style.display = 'block';
        }
      } finally {
        if (btn) {
          btn.disabled = false;
          btn.innerHTML = '<span>🚀 Sync to Supabase</span>';
        }
      }
    }

    function stepNextDate() {
      const url = new URL(window.location.href);
      const curDate = url.searchParams.get('departureDate');
      if (curDate) {
        const d = new Date(curDate + 'T00:00:00Z');
        d.setUTCDate(d.getUTCDate() + 1);
        const nextDate = d.toISOString().split('T')[0];
        url.searchParams.set('departureDate', nextDate);
        window.location.href = url.toString();
      }
    }

    document.getElementById('hso-sync-btn').addEventListener('click', () => triggerSync(true));
    document.getElementById('hso-next-btn').addEventListener('click', stepNextDate);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', createOverlay);
  } else {
    createOverlay();
  }
})();
