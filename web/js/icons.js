// Central SVG icon registry.
//
// All icons are 24x24 and render in `currentColor`, so they inherit their
// color from the surrounding text (nav links, buttons, badges). Views should
// call `icon(name)` instead of hardcoding SVG markup, which keeps icons
// consistent and gives us a single place to swap or tune artwork.
//
// The returned strings are trusted, static markup — never build them from
// user input.

const ICONS = {
  dashboard: '<svg viewBox="0 0 24 24"><path d="M4 4h7v7H4V4Zm9 0h7v7h-7V4ZM4 13h7v7H4v-7Zm9 0h7v7h-7v-7Z"/></svg>',
  projects: '<svg viewBox="0 0 24 24"><path d="M3 5.5A2.5 2.5 0 0 1 5.5 3H10l2 2h6.5A2.5 2.5 0 0 1 21 7.5v10a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 17.5v-12Zm2.5-.5a.5.5 0 0 0-.5.5v12a.5.5 0 0 0 .5.5h13a.5.5 0 0 0 .5-.5v-10a.5.5 0 0 0-.5-.5h-7.3l-2-2H5.5Z"/></svg>',
  new: '<svg viewBox="0 0 24 24"><path d="M11 4h2v7h7v2h-7v7h-2v-7H4v-2h7V4Z"/></svg>',
  publish: '<svg viewBox="0 0 24 24"><path d="M11 16V7.8L8.4 10.4 7 9l5-5 5 5-1.4 1.4L13 7.8V16h-2Zm-5 4a3 3 0 0 1-3-3v-3h2v3a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-3h2v3a3 3 0 0 1-3 3H6Z"/></svg>',
  settings: '<svg viewBox="0 0 24 24"><path d="m19.4 13.5 1.7 1.3-2 3.5-2-.8a7.8 7.8 0 0 1-1.8 1l-.3 2.1h-4l-.3-2.1a7.8 7.8 0 0 1-1.8-1l-2 .8-2-3.5 1.7-1.3a7 7 0 0 1 0-2.1L4.9 10l2-3.5 2 .8a7.8 7.8 0 0 1 1.8-1l.3-2.1h4l.3 2.1a7.8 7.8 0 0 1 1.8 1l2-.8 2 3.5-1.7 1.3a7 7 0 0 1 0 2.1ZM13 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Z"/></svg>',
  trash: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 6h18M8 6V4h8v2m-9 0 1 14h8l1-14M10 10v6m4-6v6"></path></svg>'
};

export function icon(name) {
  return ICONS[name] || "";
}
