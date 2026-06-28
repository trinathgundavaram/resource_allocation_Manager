"""
ui_common.py
------------
Small shared UI helpers.
"""

import streamlit as st


def clear_after_save(flag_key, field_keys):
    """Clear entered text in form fields after a successful save.

    Streamlit forbids writing a widget's session-state value *after* the widget
    has been created in the same run, so we defer: on a successful save the
    caller sets ``st.session_state[flag_key] = True`` and reruns; on the next
    run this function (called *before* the widgets are instantiated) drops the
    field keys so each input re-initialises to its empty default.

    Call this at the top of the form's render, then attach ``field_keys`` to the
    corresponding widgets via ``key=``.
    """
    if st.session_state.pop(flag_key, False):
        for k in field_keys:
            st.session_state.pop(k, None)
