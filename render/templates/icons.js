// Nutricore marks, drawn on a 24-unit grid in ink; a tile paints them on their colour.
// Shared by the cards and the custom-emoji tiles, so a mark in a card and in a message is the same picture.
var S = 'fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"';
var F = 'fill="currentColor"';
var ICONS = {
  // food groups (G5-R07)
  legumes: '<g ' + F + '><ellipse cx="9" cy="10" rx="4.2" ry="6" transform="rotate(-28 9 10)"/><ellipse cx="15.5" cy="14" rx="4.2" ry="6" transform="rotate(-28 15.5 14)"/></g>' +
    '<g fill="none" stroke="var(--tilebg,#fff)" stroke-width="1.4" stroke-linecap="round"><path d="M7.2 8.6c.6-1 1.5-1.6 2.4-1.8"/><path d="M13.7 12.6c.6-1 1.5-1.6 2.4-1.8"/></g>',
  anchor: '<g ' + S + '><circle cx="12" cy="5" r="2.3"/><path d="M12 7.3V21"/><path d="M8 11h8"/><path d="M4.5 14.5c0 3.6 3.4 6.5 7.5 6.5s7.5-2.9 7.5-6.5"/><path d="M3 15.8l1.5-1.3 1.5 1.3"/><path d="M18 15.8l1.5-1.3 1.5 1.3"/></g>',
  veg: '<g ' + F + '><circle cx="8" cy="9.5" r="4"/><circle cx="12.5" cy="6.8" r="4.2"/><circle cx="16.4" cy="10" r="3.8"/><circle cx="12" cy="11.2" r="3.6"/></g><path d="M10.6 13.5L10 21h4l-.6-7.5" ' + F + '/>',
  fruit: '<path d="M12 8.2c-1.6-1.4-4.6-1.8-6.4.2-2 2.2-1.6 6.2.4 9 1.6 2.3 3.6 3.5 6 2.4 2.4 1.1 4.4-.1 6-2.4 2-2.8 2.4-6.8.4-9-1.8-2-4.8-1.6-6.4-.2z" ' + F + '/>' +
    '<g ' + S + '><path d="M12 8.2c0-2 .6-3.6 2-4.8"/></g><path d="M13.5 4.6c1.6-1.6 3.8-1.6 4.6-1.2-.4 1.8-2.4 3.2-4.6 2.6z" ' + F + '/>',
  grain: '<g ' + S + '><path d="M12 22V6"/></g><g ' + F + '><ellipse cx="12" cy="4" rx="1.8" ry="2.8"/>' +
    '<ellipse cx="9" cy="8.5" rx="1.7" ry="3" transform="rotate(-40 9 8.5)"/><ellipse cx="15" cy="8.5" rx="1.7" ry="3" transform="rotate(40 15 8.5)"/>' +
    '<ellipse cx="9" cy="13" rx="1.7" ry="3" transform="rotate(-40 9 13)"/><ellipse cx="15" cy="13" rx="1.7" ry="3" transform="rotate(40 15 13)"/>' +
    '<ellipse cx="9" cy="17.5" rx="1.7" ry="3" transform="rotate(-40 9 17.5)"/><ellipse cx="15" cy="17.5" rx="1.7" ry="3" transform="rotate(40 15 17.5)"/></g>',
  nuts: '<path d="M9.5 3.5c3 0 4.6 2.4 4.2 5-.2 1.2.2 2 1.2 2.6 2.4 1.4 3.4 3.6 2.6 6.2-1 3-4.6 4.4-7.6 3-2.4-1.1-3.4-3.4-3-5.8.2-1.2-.2-2-1-2.8-1.8-1.8-1.8-4.6-.2-6.4.8-1.1 2.2-1.8 3.8-1.8z" ' + F + '/>' +
    '<g fill="var(--tilebg,#fff)"><circle cx="9.5" cy="7" r=".9"/><circle cx="11.8" cy="9" r=".9"/><circle cx="12.4" cy="15.2" r=".9"/><circle cx="14.6" cy="17.4" r=".9"/></g>',
  calcium: '<path d="M8 2.5h8v3l2 3.2V20a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 6 20V8.7l2-3.2z" ' + F + '/>' +
    '<path d="M6.6 11.5h10.8v5H6.6z" fill="var(--tilebg,#fff)"/><path d="M9.2 14l1.4-1.4 1.4 1.4 1.4-1.4 1.4 1.4" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/>',
  omega: '<path d="M12 2.5c3.6 4.4 6.5 8.2 6.5 11.7a6.5 6.5 0 0 1-13 0c0-3.5 2.9-7.3 6.5-11.7z" ' + F + '/>' +
    '<path d="M12 21v-6.5m0 0c0-2 1.4-3.5 3.4-3.5 0 2-1.4 3.5-3.4 3.5zm0 0c0-1.6-1.1-2.8-2.7-2.8 0 1.6 1.1 2.8 2.7 2.8z" fill="var(--tilebg,#fff)" stroke="var(--tilebg,#fff)" stroke-width="1.2" stroke-linejoin="round"/>',
  // the cards' other marks
  plants: '<g ' + S + '><path d="M12 21v-9"/></g><path d="M12 12.5C12 8 9 5.5 4 5.5c0 4.5 3 7 8 7z" ' + F + '/><path d="M12 10.5c0-4 2.8-6.5 8-6.5 0 4-2.8 6.5-8 6.5z" ' + F + '/>',
  clock: '<g ' + S + '><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.2 2"/></g>',
  step: '<g ' + F + '><ellipse cx="8.2" cy="14.5" rx="3" ry="4.4"/><ellipse cx="15.8" cy="9" rx="3" ry="4.4"/><circle cx="8.2" cy="20.6" r="1.6"/><circle cx="15.8" cy="15.1" r="1.6"/></g>',
  routine: '<g transform="rotate(-40 12 12)"><rect x="3" y="8" width="18" height="8" rx="4" ' + F + '/><path d="M12 8h5a4 4 0 0 1 0 8h-5z" fill="var(--tilebg,#fff)"/><rect x="3" y="8" width="18" height="8" rx="4" fill="none" stroke="currentColor" stroke-width="2"/></g>',
  check: '<g ' + S + ' stroke-width="3"><path d="M5 12.5l4.5 4.5L19 7.5"/></g>',
  spark: '<path d="M12 2.5l2.1 6.2 6.4 1.8-6.4 1.9L12 18.6l-2.1-6.2-6.4-1.9 6.4-1.8z" ' + F + '/><path d="M18.5 15.5l.9 2.2 2.1.8-2.1.8-.9 2.2-.9-2.2-2.1-.8 2.1-.8z" ' + F + '/>',
  flask: '<g ' + S + '><path d="M9 3h6"/><path d="M10 3v6l-5.2 9.2A1.9 1.9 0 0 0 6.5 21h11a1.9 1.9 0 0 0 1.7-2.8L14 9V3"/></g><path d="M7.4 15h9.2l2 3.4a.9.9 0 0 1-.8 1.4H6.2a.9.9 0 0 1-.8-1.4z" ' + F + '/>',
  ladder: '<g ' + S + '><path d="M7 2.5v19"/><path d="M17 2.5v19"/><path d="M7 6.5h10"/><path d="M7 11h10"/><path d="M7 15.5h10"/><path d="M7 20h10"/></g>',
  basket: '<g ' + S + '><path d="M8 10l3-6"/><path d="M16 10l-3-6"/></g><path d="M2.5 9.5h19l-2.2 9.6a2 2 0 0 1-2 1.6H6.7a2 2 0 0 1-2-1.6z" ' + F + '/>' +
    '<g stroke="var(--tilebg,#fff)" stroke-width="1.6" stroke-linecap="round"><path d="M8.5 13v4.5"/><path d="M12 13v4.5"/><path d="M15.5 13v4.5"/></g>',
  hand: '<path d="M7 12V6.2a1.5 1.5 0 0 1 3 0V11m0-5.8V4.5a1.5 1.5 0 0 1 3 0V11m0-5.5a1.5 1.5 0 0 1 3 0V12m0-4a1.5 1.5 0 0 1 3 0v6.5c0 4-3 7-7 7-2.6 0-4.4-1.2-5.8-3.2L3.8 14a1.5 1.5 0 0 1 2.4-1.8L7 13.4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>',
  export: '<g ' + S + '><path d="M12 15V3"/><path d="M7.5 7.5L12 3l4.5 4.5"/><path d="M4 13v5.5A2.5 2.5 0 0 0 6.5 21h11a2.5 2.5 0 0 0 2.5-2.5V13"/></g>',
  plate: '<g ' + S + '><circle cx="13.5" cy="12" r="7.5"/><circle cx="13.5" cy="12" r="3.8"/><path d="M3 3.5v6.5"/><path d="M3 10a1.6 1.6 0 0 0 0 0"/><path d="M1.6 3.5v4a1.4 1.4 0 0 0 2.8 0v-4"/><path d="M3 9v11.5"/></g>',
  chart: '<g ' + S + ' stroke-width="2.6"><path d="M5 20V12"/><path d="M10 20V5"/><path d="M15 20v-9"/><path d="M20 20V8"/></g>',
  numbers: '<rect x="2" y="5" width="20" height="14" rx="4" ' + F + '/><text x="12" y="16.2" text-anchor="middle" font-family="Nunito" font-weight="1000" font-size="9" fill="var(--tilebg,#fff)">123</text>',
  swap: '<g ' + S + '><path d="M4 9a8 8 0 0 1 14-3.5L20 8"/><path d="M20 3v5h-5"/><path d="M20 15a8 8 0 0 1-14 3.5L4 16"/><path d="M4 21v-5h5"/></g>',
  later: '<g ' + S + '><path d="M3 9h6l-6 7h6"/><path d="M13 4h5l-5 6h5"/></g><g ' + S + ' stroke-width="1.8"><path d="M16 15h4l-4 5h4"/></g>',
  battery: '<rect x="2" y="7" width="18" height="10" rx="2.5" fill="none" stroke="currentColor" stroke-width="2.2"/><rect x="20.4" y="10" width="2" height="4" rx="1" ' + F + '/><rect x="4.5" y="9.5" width="13" height="5" rx="1" ' + F + '/>',
  lowbat: '<rect x="2" y="7" width="18" height="10" rx="2.5" fill="none" stroke="currentColor" stroke-width="2.2"/><rect x="20.4" y="10" width="2" height="4" rx="1" ' + F + '/><rect x="4.5" y="9.5" width="4" height="5" rx="1" ' + F + '/>',
  sofa: '<path d="M5 11V8a3 3 0 0 1 3-3h8a3 3 0 0 1 3 3v3" fill="none" stroke="currentColor" stroke-width="2.2"/><path d="M2 12a2 2 0 0 1 4 0v2h12v-2a2 2 0 0 1 4 0v5a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2z" ' + F + '/><g ' + S + '><path d="M5 19v2"/><path d="M19 19v2"/></g>',
  done: '<rect x="1.5" y="1.5" width="21" height="21" rx="6" fill="#3F8F5A"/><path d="M6.5 12.5l3.7 3.7 7.3-7.7" fill="none" stroke="#FFFDF8" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>',
  gear: '<g ' + S + '><circle cx="12" cy="12" r="3.2"/><path d="M12 2.8v2.6M12 18.6v2.6M2.8 12h2.6M18.6 12h2.6M5.5 5.5l1.8 1.8M16.7 16.7l1.8 1.8M5.5 18.5l1.8-1.8M16.7 7.3l1.8-1.8"/><circle cx="12" cy="12" r="6.6"/></g>',
  star: '<path d="M12 2.8l2.8 5.8 6.3.9-4.6 4.4 1.1 6.3L12 17.2l-5.6 3 1.1-6.3L2.9 9.5l6.3-.9z" ' + F + '/>'
};
// The colour of each group's tile: warm, earthy, never red or success-green (rule 5: no judging colours on food).
var GROUPS = {
  legumes: { name: "Бобовые и соя", short: "бобовые", fill: "#E2A33B" },
  anchor: { name: "Белковая опора", short: "опора", fill: "#8FA9D3" },
  veg: { name: "Овощи", short: "овощи", fill: "#6DB8A8" },
  fruit: { name: "Фрукты и ягоды", short: "фрукты", fill: "#EFA27A" },
  grain: { name: "Цельные злаки", short: "цельные злаки", fill: "#D8B95E" },
  nuts: { name: "Орехи и семечки", short: "орехи", fill: "#C09468" },
  calcium: { name: "Кальций", short: "кальций", fill: "#A9CBE3" },
  omega: { name: "Омега-3 из растений", short: "омега-3", fill: "#B3A3DA" }
};
var MARKS = {
  plants: "#9CCB86", clock: "#E7DFD1", step: "#F2C27B", routine: "#E9B8C9", spark: "#F3D27A", flask: "#B9D6C9",
  ladder: "#E5C7A0", basket: "#E3C08F", hand: "#E8D3C0", export: "#BFD0E4", plate: "#E7DFD1", chart: "#C8D7E8",
  numbers: "#D6D0C4", swap: "#CFE0D9", later: "#D9D3EA", battery: "#CBE3B4", lowbat: "#F0D59C", sofa: "#E2CDE6",
  gear: "#D9D3C9", star: "#F5D88A", check: "#E7DFD1", done: "transparent"
};
function svg(name) {
  return '<svg class="ico" viewBox="0 0 24 24" aria-hidden="true">' + ICONS[name] + "</svg>";
}
function tile(name, size, pad) {
  // Sizes in px: a percentage padding would be read against the panel's width, not the tile's.
  size = size || 28;
  var t = document.createElement("span");
  t.className = "tile";
  var fill = GROUPS[name] ? GROUPS[name].fill : MARKS[name];
  t.style.setProperty("--tilebg", fill);
  t.style.background = fill;
  t.style.width = t.style.height = size + "px";
  t.style.padding = (pad != null ? pad : Math.round(size * 0.17)) + "px";
  t.style.borderRadius = Math.round(size * 0.26) + "px";
  t.innerHTML = svg(name); // constants only: never user text
  return t;
}
