/* QuonTech Asset Management - site behaviour */

// Email that receives messages from the contact form.
// Leave empty until the team decides on a shared address.
const CONTACT_EMAIL = "";

document.addEventListener("DOMContentLoaded", () => {
  /* ---------- Intro curtain (only on pages that contain #intro) ---------- */
  const intro = document.getElementById("intro");
  if (intro) {
    let seen = false;
    try { seen = sessionStorage.getItem("qt-intro-seen") === "1"; } catch (e) {}
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    if (seen || reduce) {
      intro.classList.add("is-gone");
    } else {
      document.body.classList.add("intro-lock");
      const open = () => {
        if (intro.classList.contains("is-open")) return;
        intro.classList.add("is-open");
        document.body.classList.remove("intro-lock");
        try { sessionStorage.setItem("qt-intro-seen", "1"); } catch (e) {}
        setTimeout(() => intro.classList.add("is-gone"), 1200);
      };
      setTimeout(open, 2000);                 // curtain opens on its own
      intro.addEventListener("click", open);  // or on click
      window.addEventListener("wheel", open, { once: true, passive: true });
    }
  }

  /* ---------- Mobile menu ---------- */
  const toggle = document.querySelector(".nav-toggle");
  const nav = document.querySelector(".nav");
  if (toggle && nav) {
    toggle.addEventListener("click", () => {
      const isOpen = nav.classList.toggle("open");
      toggle.setAttribute("aria-expanded", String(isOpen));
    });
  }

  /* ---------- Reveal on scroll ---------- */
  const items = document.querySelectorAll(".reveal");
  if ("IntersectionObserver" in window) {
    const io = new IntersectionObserver((entries) => {
      entries.forEach((e) => {
        if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); }
      });
    }, { threshold: 0.15 });
    items.forEach((el) => io.observe(el));
  } else {
    items.forEach((el) => el.classList.add("in"));
  }

  /* ---------- Back to top ---------- */
  const toTop = document.querySelector(".to-top");
  if (toTop) {
    const onScroll = () => toTop.classList.toggle("show", window.scrollY > 700);
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
    toTop.addEventListener("click", () => window.scrollTo({ top: 0, behavior: "smooth" }));
  }

  /* ---------- Document buttons: enabled automatically when the PDF exists ---------- */
  document.querySelectorAll("[data-report]").forEach(async (btn) => {
    const url = btn.getAttribute("data-report");
    try {
      const res = await fetch(url, { method: "HEAD", cache: "no-store" });
      if (res.ok) {
        btn.href = url;
        btn.textContent = "Download";
        btn.classList.remove("is-disabled");
        btn.setAttribute("download", "");
      }
    } catch (e) { /* keep "Coming soon" */ }
  });

  /* ---------- Team photos: show photo if team/<name>.jpg exists ---------- */
  document.querySelectorAll(".member .photo img").forEach((img) => {
    img.addEventListener("error", () => img.remove());
  });

  /* ---------- Contact form (opens the visitor's email app) ---------- */
  const form = document.querySelector(".form");
  if (form) {
    form.addEventListener("submit", (ev) => {
      ev.preventDefault();
      const note = form.querySelector(".form-note");
      if (!CONTACT_EMAIL) {
        note.textContent = "Our contact email will be available soon. Thank you for your interest.";
        return;
      }
      const d = new FormData(form);
      const subject = encodeURIComponent("Website enquiry from " + d.get("name") + " " + d.get("surname"));
      const body = encodeURIComponent(d.get("message") + "\n\n" + d.get("name") + " " + d.get("surname") + "\n" + d.get("email"));
      window.location.href = "mailto:" + CONTACT_EMAIL + "?subject=" + subject + "&body=" + body;
      note.textContent = "Thank you, your email app is opening.";
    });
  }
});
