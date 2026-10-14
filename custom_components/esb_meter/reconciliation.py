"""Optional recorder-backed comparison, run only after HA has started."""
import logging
from datetime import timedelta
from homeassistant.core import CoreState
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.exceptions import HomeAssistantError
from .comparison import compare_day, day_window

_LOGGER = logging.getLogger(__name__)


async def async_comparison(hass, options, overview):
    results = {}
    for direction in ('import','export'):
        source = options.get('comparison_'+direction)
        section = overview.get(direction,{})
        label = section.get('latest_complete_day')
        if not source:
            results[direction] = {'status':'not_configured'}
            continue
        if not label:
            results[direction] = {'status':'incomplete_esb'}
            continue
        if hass.state is not CoreState.running:
            results[direction] = {'status':'waiting_for_startup','date':label}
            continue
        start,end = day_window(label)
        try:
            # Explicit conversion uses recorded metadata, including MWh sources.
            rows = await get_instance(hass).async_add_executor_job(
                statistics_during_period,hass,start-timedelta(hours=1),end,{source},'hour',{'energy':'kWh'},{'sum','change'})
            results[direction] = compare_day(label,section['latest_day_kwh'],rows.get(source,[]),
                options.get('comparison_absolute',0.5),options.get('comparison_percent',5.0))
            results[direction]['reference_entity'] = source
        except Exception:
            # Comparison must not break retrieval or leak exception text/account details.
            _LOGGER.warning('ESB %s comparison could not read compatible recorder history',direction)
            results[direction] = {'status':'reference_error','date':label}
    return results
