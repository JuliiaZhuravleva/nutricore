// The marks only the custom emoji need, in the idiom of ../templates/icons.js and loaded after it.
// Apart from that file on purpose: a new mark here cannot move a pixel in a card.
var BG = 'var(--tilebg,#fff)';
var CUT = 'fill="none" stroke="' + BG + '" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"';
var HEAVY = S + ' stroke-width="3"';
var DROP = '<path d="M12 2.5c3.8 4.8 7 8.6 7 12.2a7 7 0 0 1-14 0c0-3.6 3.2-7.4 7-12.2z" ' + F + '/><path d="M8.7 15a3.4 3.4 0 0 0 2.6 3.2" ' + CUT + '/>';
var CROSS = '<g ' + HEAVY + '><path d="M6 6l12 12"/><path d="M18 6L6 18"/></g>';
var CAL = '<rect x="3" y="5" width="18" height="16" rx="3" fill="none" stroke="currentColor" stroke-width="2.2"/><path d="M3 8a3 3 0 0 1 3-3h12a3 3 0 0 1 3 3v2.5H3z" ' + F + '/><g ' + S + '><path d="M8 2.5v4"/><path d="M16 2.5v4"/></g>';

Object.assign(ICONS, {
  // moving around
  back: '<g ' + HEAVY + '><path d="M10 5.5L3.5 12l6.5 6.5"/><path d="M4 12h16.5"/></g>',
  undo: '<g ' + HEAVY + ' stroke-width="2.8"><path d="M8.5 4.5L4 9l4.5 4.5"/><path d="M4.5 9H14a6 6 0 0 1 0 12h-3"/></g>',
  prev: '<path d="M16 4.5L6.5 12l9.5 7.5z" ' + F + ' stroke="currentColor" stroke-width="2.2" stroke-linejoin="round"/>',
  next: '<path d="M8 4.5l9.5 7.5L8 19.5z" ' + F + ' stroke="currentColor" stroke-width="2.2" stroke-linejoin="round"/>',
  plus: '<g ' + HEAVY + '><path d="M12 4.5v15"/><path d="M4.5 12h15"/></g>',
  minus: '<g ' + HEAVY + '><path d="M4.5 12h15"/></g>',
  cross: CROSS,
  reject: CROSS,
  repeat: '<g ' + S + ' stroke-width="2.6"><path d="M4 11V9.5A3.5 3.5 0 0 1 7.5 6H20"/><path d="M16.5 2.5L20 6l-3.5 3.5"/><path d="M20 13v1.5a3.5 3.5 0 0 1-3.5 3.5H4"/><path d="M7.5 14.5L4 18l3.5 3.5"/></g>',
  link: '<g ' + S + ' stroke-width="2.6"><path d="M10 14a4.5 4.5 0 0 0 6.4 0l3-3a4.5 4.5 0 0 0-6.4-6.4l-1 1"/><path d="M14 10a4.5 4.5 0 0 0-6.4 0l-3 3a4.5 4.5 0 0 0 6.4 6.4l1-1"/></g>',
  search: '<g ' + S + ' stroke-width="2.6"><circle cx="10.5" cy="10.5" r="6.5"/><path d="M15.5 15.5L21 21"/></g>',
  // things to do with a record
  pencil: '<path d="M4 20l1.2-4.8L16.4 4a2.1 2.1 0 0 1 3 0l.6.6a2.1 2.1 0 0 1 0 3L8.8 18.8z" ' + F + '/><path d="M14.5 6l3.5 3.5" ' + CUT + '/>',
  trash: '<g ' + S + '><path d="M4 6.5h16"/><path d="M9 6V3.8h6V6"/></g><path d="M6 8.8h12l-1 11.4a1.5 1.5 0 0 1-1.5 1.3h-7A1.5 1.5 0 0 1 7 20.2z" ' + F + '/>',
  tag: '<path d="M3 5a2 2 0 0 1 2-2h7.2a2 2 0 0 1 1.4.6l7.3 7.3a2 2 0 0 1 0 2.8l-7.2 7.2a2 2 0 0 1-2.8 0L3.6 13.6A2 2 0 0 1 3 12.2z" ' + F + '/><circle cx="7.8" cy="7.8" r="1.8" fill="' + BG + '"/>',
  clipboard: '<rect x="4.5" y="4" width="15" height="18" rx="2.5" ' + F + '/><rect x="8" y="1.8" width="8" height="5" rx="1.6" ' + F + ' stroke="' + BG + '" stroke-width="1.2"/><g ' + CUT + '><path d="M8 12h8"/><path d="M8 16.5h8"/></g>',
  symbols: '<rect x="3.5" y="3.5" width="7" height="7" rx="1.5" ' + F + '/><circle cx="17" cy="7" r="3.6" ' + F + '/><path d="M7 13.5l4 7H3z" ' + F + ' stroke="currentColor" stroke-linejoin="round"/><g ' + S + '><path d="M17 14v7"/><path d="M13.5 17.5h7"/></g>',
  // what a record says
  warn: '<path d="M12 4L21.5 20.5h-19z" ' + F + ' stroke="currentColor" stroke-width="2.4" stroke-linejoin="round"/><path d="M12 10v4.8" fill="none" stroke="' + BG + '" stroke-width="2.4" stroke-linecap="round"/><circle cx="12" cy="18" r="1.4" fill="' + BG + '"/>',
  stop: '<circle cx="12" cy="12" r="10" ' + F + '/><rect x="6" y="10" width="12" height="4" rx="1.5" fill="' + BG + '"/>',
  lock: '<rect x="5" y="10.5" width="14" height="11" rx="2.5" ' + F + '/><g ' + S + '><path d="M8 10.5V7.5a4 4 0 0 1 8 0v3"/></g>',
  hourglass: '<g ' + S + '><path d="M5.5 3h13"/><path d="M5.5 21h13"/><path d="M7 3.5c0 4 3 5.5 5 8.5 2-3 5-4.5 5-8.5"/><path d="M7 20.5c0-4 3-5.5 5-8.5 2 3 5 4.5 5 8.5"/></g><path d="M8.6 5.5h6.8c-.6 2-2 3.1-3.4 4.8-1.4-1.7-2.8-2.8-3.4-4.8z" ' + F + '/>',
  question: '<g ' + HEAVY + '><path d="M8 8.5a4 4 0 1 1 6.2 3.4c-1.4.9-2.2 1.7-2.2 3.3"/></g><circle cx="12" cy="20" r="1.8" ' + F + '/>',
  thumb: '<rect x="2.5" y="10.5" width="4.5" height="10.5" rx="1.2" ' + F + '/><path d="M9 10.5l3.5-7.5c1.8 0 2.8 1.2 2.4 3l-.7 3.2h5a2 2 0 0 1 2 2.4l-1.3 6.8a2 2 0 0 1-2 1.6H9z" ' + F + '/>',
  wave: '<g transform="rotate(18 12 12)">' + ICONS.hand + '</g>',
  dizzy: ICONS.spark,
  // the cards' plate with 123 turns to a smudge at 20 px, so the emoji says it larger
  digits: '<text x="12" y="17.5" text-anchor="middle" font-family="Nunito" font-weight="1000" font-size="16" fill="currentColor">12</text>',
  // the meal, the body
  scale: '<g ' + S + '><path d="M12 3.5V20"/><path d="M7.5 20.5h9"/><path d="M4.5 6.5h15"/></g><path d="M4.5 6.5L1.8 13.5a3 3 0 0 0 5.4 0z" ' + F + ' stroke="currentColor" stroke-linejoin="round"/><path d="M19.5 6.5l-2.7 7a3 3 0 0 0 5.4 0z" ' + F + ' stroke="currentColor" stroke-linejoin="round"/>',
  dish: '<path d="M3 11h18a9 9 0 0 1-6 8.5V21H9v-1.5A9 9 0 0 1 3 11z" ' + F + '/><g ' + S + '><path d="M9 7.5c0-1.6 1.5-1.6 1.5-3.5"/><path d="M14 7.5c0-1.6 1.5-1.6 1.5-3.5"/></g>',
  fire: '<path d="M12 2.5c.6 3.6 5.5 6 6.5 10.8.9 4.4-2 8.2-6.5 8.2s-7.4-3.4-6.5-7.6c.4-2 1.6-3.2 2.6-4.4.2 1.6.8 2.6 1.8 3.2C9.4 9 10 5.4 12 2.5z" ' + F + '/>',
  can: '<path d="M5 6.5v11c0 1.9 3.1 3.5 7 3.5s7-1.6 7-3.5v-11z" ' + F + '/><ellipse cx="12" cy="6" rx="7" ry="2.9" fill="' + BG + '" stroke="currentColor" stroke-width="2.2"/><path d="M5.5 14.5c1.6 1.2 3.9 1.8 6.5 1.8s4.9-.6 6.5-1.8" ' + CUT + '/>',
  carrot: '<path d="M5 20c-1-1-.8-2.2 0-3.4L13 7.5c1.4-1.4 3.6-1.4 4.9 0s1.3 3.4-.1 4.8L8.4 20.2C7.4 21 6 21 5 20z" ' + F + '/><g ' + S + '><path d="M17.5 6.5L21 3"/><path d="M15.5 5.5L16.5 2"/><path d="M18.5 8.5l3.5-1"/></g>',
  salt: '<path d="M7.5 10.5h9l1.2 9a2 2 0 0 1-2 2.3H8.3a2 2 0 0 1-2-2.3z" ' + F + '/><path d="M7.8 8.5a4.2 4.2 0 0 1 8.4 0z" ' + F + '/><g fill="' + BG + '"><circle cx="10.3" cy="6.7" r=".9"/><circle cx="13.7" cy="6.7" r=".9"/></g>',
  drop: DROP,
  blood: DROP,
  sun: '<circle cx="12" cy="12" r="4.8" ' + F + '/><g ' + S + '><path d="M12 2.5v2.4"/><path d="M12 19.1v2.4"/><path d="M2.5 12h2.4"/><path d="M19.1 12h2.4"/><path d="M5.3 5.3L7 7"/><path d="M17 17l1.7 1.7"/><path d="M5.3 18.7L7 17"/><path d="M17 7l1.7-1.7"/></g>',
  walk: '<circle cx="13.5" cy="4" r="2.3" ' + F + '/><g ' + S + ' stroke-width="2.6"><path d="M13 8.5l-1.5 6 3.5 3 1 4"/><path d="M11.5 14.5L9.5 21"/><path d="M13 8.5l-4 2-1 3.5"/><path d="M13 8.5l2.5 3 3.5 1"/></g>',
  lotus: '<circle cx="12" cy="5" r="2.5" ' + F + '/><g ' + S + ' stroke-width="2.6"><path d="M12 9.5v5.5"/><path d="M12 9.5l-5.5 5"/><path d="M12 9.5l5.5 5"/><path d="M4 20c3-2.8 5.2-4 8-4s5 1.2 8 4"/></g>',
  // time
  clock9: '<g ' + S + '><circle cx="12" cy="12" r="9"/><path d="M12 7v5H7.5"/></g>',
  calendar: CAL + '<g ' + F + '><circle cx="8" cy="15" r="1.5"/><circle cx="12" cy="15" r="1.5"/><circle cx="16" cy="15" r="1.5"/></g>',
  calendar2: CAL + '<g ' + S + '><path d="M7.5 15.5l3 2.5 5.5-5"/></g>',
  // the service around the record
  target: '<g ' + S + '><circle cx="12" cy="12" r="9.5"/><circle cx="12" cy="12" r="4.8"/></g><circle cx="12" cy="12" r="1.5" ' + F + '/>',
  gem: '<path d="M7 3.5h10l4.5 6L12 21 2.5 9.5z" ' + F + '/><g ' + CUT + ' stroke-width="1.4"><path d="M2.5 9.5h19"/><path d="M8.5 9.5L12 21l3.5-11.5"/></g>',
  money: '<circle cx="12" cy="12" r="10" ' + F + '/><g ' + CUT + ' stroke-width="2"><path d="M15.5 8.8c-.7-1-1.9-1.6-3.5-1.6-2 0-3.4 1-3.4 2.4 0 3.4 7 1.4 7 4.8 0 1.4-1.5 2.4-3.6 2.4-1.7 0-3-.6-3.6-1.8"/><path d="M12 5.5v13"/></g>',
  bankcard: '<rect x="2" y="5" width="20" height="14" rx="3" ' + F + '/><rect x="2" y="8.5" width="20" height="3" fill="' + BG + '"/><rect x="5" y="14.5" width="5.5" height="1.8" rx=".6" fill="' + BG + '"/>',
  box: '<path d="M3 8l9-5 9 5v9l-9 5-9-5z" ' + F + ' stroke="currentColor" stroke-linejoin="round"/><path d="M3.5 8l8.5 5 8.5-5M12 13v8.5" ' + CUT + '/>',
  phone: '<rect x="6.5" y="2" width="11" height="20" rx="2.5" ' + F + '/><rect x="8.5" y="5" width="7" height="12" rx=".6" fill="' + BG + '"/>',
  globe: '<g ' + S + '><circle cx="12" cy="12" r="9"/><path d="M3 12h18"/><ellipse cx="12" cy="12" rx="4" ry="9"/></g>',
  robot: '<rect x="4" y="8" width="16" height="12.5" rx="3.5" ' + F + '/><g ' + S + '><path d="M12 8V4.5"/></g><circle cx="12" cy="3" r="1.7" ' + F + '/><g fill="' + BG + '"><circle cx="9" cy="14" r="1.8"/><circle cx="15" cy="14" r="1.8"/></g>',
  camera: '<path d="M3 8.5A2.5 2.5 0 0 1 5.5 6h2.2l1.4-2.2h5.8L16.3 6h2.2A2.5 2.5 0 0 1 21 8.5V18a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 18z" ' + F + '/><circle cx="12" cy="13" r="4.4" fill="' + BG + '"/><circle cx="12" cy="13" r="2.3" ' + F + '/>',
  mic: '<rect x="8.5" y="2.5" width="7" height="12" rx="3.5" ' + F + '/><g ' + S + '><path d="M5 11.5a7 7 0 0 0 14 0"/><path d="M12 18.5v3"/><path d="M8.5 21.5h7"/></g>',
  brain: '<path d="M11 3.5a3.5 3.5 0 0 0-3.4 2.8A3.5 3.5 0 0 0 5 12a3.5 3.5 0 0 0 1.6 5.8A3.5 3.5 0 0 0 11 20.5z" ' + F + '/><path d="M13 3.5a3.5 3.5 0 0 1 3.4 2.8A3.5 3.5 0 0 1 19 12a3.5 3.5 0 0 1-1.6 5.8A3.5 3.5 0 0 1 13 20.5z" ' + F + '/>'
});
// Warm and earthy like the rest; a warning is amber and a refusal dusty, never red (G5: no judging colours).
Object.assign(MARKS, {
  back: "#DDD7CB", undo: "#DAD3EA", prev: "#DDD7CB", next: "#DDD7CB", plus: "#CDE3C2", minus: "#E5DCCB",
  cross: "#E5DCCB", reject: "#EBC7B3", repeat: "#CFE0D9", link: "#C8D7E8", search: "#C8D7E8",
  pencil: "#F2DDA0", trash: "#E5DCCB", tag: "#E9C9A6", clipboard: "#D9D3C9", symbols: "#E2CDE6",
  warn: "#F2CF85", stop: "#EBC7B3", lock: "#D6D0C4", hourglass: "#E9D9B4", question: "#D9D3EA",
  thumb: "#CDE3C2", wave: "#F2D6A8", dizzy: "#D9D3EA", digits: "#D6D0C4",
  scale: "#C8D7E8", dish: "#F0C79A", fire: "#F2B98A", can: "#E3C08F", carrot: "#F2B98A", salt: "#D9DEE4",
  drop: "#A9CBE3", blood: "#E3A9A0", sun: "#F5D88A", walk: "#CBE3B4", lotus: "#E2CDE6",
  clock9: "#E7DFD1", calendar: "#C8D7E8", calendar2: "#CFE0D9", target: "#F2C27B", gem: "#BFD0E4",
  money: "#D9CF9E", bankcard: "#BFD0E4", box: "#E3C08F", phone: "#D6D0C4", globe: "#BFD0E4",
  robot: "#CFE0D9", camera: "#D6D0C4", mic: "#E9B8C9", brain: "#E2CDE6"
});
