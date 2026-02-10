# Navigation Scroll State Preservation

## What It Does

Preserves the scroll position in the navigation panel when lists update via SSE (Server-Sent Events). Without this, the navigation would jump back to the top every time tasks, orchestrators, or sessions update, which is very annoying for users.

## Implementation

### Pattern Used (in all render functions):

```javascript
function renderTasks(tasks) {
  if (!tasksList) return;
  const sessionsPanel = document.querySelector(".sessions");
  const prevScroll = sessionsPanel ? sessionsPanel.scrollTop : 0;  // SAVE

  // ... render tasks HTML ...

  if (sessionsPanel) {
    sessionsPanel.scrollTop = prevScroll;  // RESTORE
  }
}
```

### Applied In:

1. **renderTasks()** - chat.html lines 3889-3946
   - Saves scroll position before rendering task list
   - Restores after tasks are rendered
   - Triggered by SSE task updates

2. **renderOrchestrators()** - chat.html lines 3948-4003
   - Saves scroll position before rendering orchestrator list
   - Restores after orchestrators are rendered
   - Triggered by SSE orchestrator updates

3. **renderSessions()** - chat.html lines 4591-4649
   - Saves scroll position before rendering session list
   - Restores after sessions are rendered
   - Triggered by SSE session updates

## Why This Matters

### Without Scroll Preservation:
❌ User scrolls down to see task #20
❌ SSE update arrives (task status changes)
❌ List re-renders
❌ Scroll jumps back to top
❌ User loses their place
❌ Very frustrating for long lists

### With Scroll Preservation:
✅ User scrolls down to see task #20
✅ SSE update arrives (task status changes)
✅ Scroll position saved (e.g., 450px)
✅ List re-renders
✅ Scroll position restored (450px)
✅ User stays at task #20
✅ Seamless experience

## Technical Details

### Element Used:
```javascript
const sessionsPanel = document.querySelector(".sessions");
```

This is the parent container with `overflow-y: auto` that handles scrolling for:
- Sessions list
- Tasks list
- Orchestrators list

All three lists share the same scrollable container.

### CSS (chat.html lines ~310-320):
```css
.sessions {
  display: flex;
  flex-direction: column;
  max-height: calc(100vh - 96px);
  overflow-y: auto;  /* This element scrolls */
}

.sessions ul {
  overflow: visible;  /* Remove scrollbar from inner list */
}
```

### Why It Works:
1. Only the parent `.sessions` container scrolls
2. Inner `ul` elements don't have their own scrollbars
3. All lists are contained in the same scrollable area
4. Saving/restoring `scrollTop` on the parent affects all lists

## Edge Cases Handled

1. **Panel doesn't exist**: Check `sessionsPanel` exists before save/restore
2. **No previous scroll**: Defaults to 0 if panel not found
3. **Empty lists**: Still preserves scroll position
4. **Rapid updates**: Each update saves current position before rendering

## Performance

- **Very lightweight**: Just two property accesses per render
- **No layout thrashing**: Read before write (save → render → restore)
- **Synchronous**: No async delays or animations

## Related CSS

The navigation scrolling setup:
```css
.sessions {
  max-height: calc(100vh - 96px);
  overflow-y: auto;
}

.sessions ul {
  overflow: visible;  /* No scrollbar on list itself */
  max-height: none;   /* Let parent handle scrolling */
}

ul.sessions-list {
  overflow: visible;
  max-height: none;
}
```

## Testing

To verify it works:
1. Create many tasks (20+) so list scrolls
2. Scroll down to bottom of task list
3. Run a task or toggle enabled/disabled
4. SSE update will trigger re-render
5. ✅ Scroll position should stay at bottom
6. ❌ Without this fix, it would jump to top

## Benefits

✅ **Better UX** - No jarring scroll jumps
✅ **Real-time updates** - Lists can update without disruption
✅ **Long lists** - Works well with many items
✅ **Consistent** - Same pattern across all list types
✅ **Simple** - Easy to understand and maintain

## Notes

- This is standard practice for real-time updating lists
- Works with SSE (Server-Sent Events) architecture
- Same pattern used in modern web apps (Slack, Discord, etc.)
- Could be enhanced with smooth scrolling or scroll restoration on initial page load
