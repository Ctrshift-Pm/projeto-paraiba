(function () {
  const body = document.body;
  const toggle = document.querySelector("[data-nav-toggle]");
  const panel = document.querySelector("[data-nav-panel]");
  const backdrop = document.querySelector("[data-nav-backdrop]");

  if (!toggle || !panel || !backdrop) {
    return;
  }

  function setOpen(isOpen) {
    body.classList.toggle("nav-open", isOpen);
    toggle.setAttribute("aria-expanded", isOpen ? "true" : "false");
    backdrop.hidden = !isOpen;
    document.documentElement.classList.toggle("nav-lock-scroll", isOpen);
  }

  toggle.addEventListener("click", () => {
    setOpen(!body.classList.contains("nav-open"));
  });

  backdrop.addEventListener("click", () => setOpen(false));

  panel.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof Element)) {
      return;
    }
    if (target.closest("a, button, label")) {
      setOpen(false);
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      setOpen(false);
    }
  });

  window.addEventListener("resize", () => {
    if (window.innerWidth > 760) {
      setOpen(false);
    }
  });
})();
