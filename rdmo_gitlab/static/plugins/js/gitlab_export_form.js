function select_all_exports(choice_count) {
    // console.log('select_all_exports()')
    var checkBox = document.getElementById('id_all_exports');

    for (let i=0; i<choice_count; i++) {
        var choice = document.getElementById(`id_exports_${i}`);
        choice.checked = checkBox.checked;
        
        var file_path_span = document.getElementById(`id_exports_file_path_${i}`);
        file_path_span.style.display = checkBox.checked ? 'flex' : 'none';

        var check_message_span = document.getElementById(`id_exports_check_message_${i}`);
        check_message_span.style.display = checkBox.checked ? 'inline' : 'none';
    }
}

function toggle_option_attributes_visibility(element) {
    // console.log('show_file_path()')
    // console.log(element)
    const index = element.id.replace('id_exports_', '')
    
    var file_path_span = document.getElementById(`id_exports_file_path_${index}`);
    file_path_span.style.display = element.checked ? 'flex' : 'none';

    var check_message_span = document.getElementById(`id_exports_check_message_${index}`);
    check_message_span.style.display = element.checked ? 'inline' : 'none';
}

function hide_check_message(element) {
    // console.log('hide_check_message()')
    // console.log(element)
    const index = element.id.replace('id_exports_text_', '')

    var duration = 1000;
    clearTimeout(element._timer);
    element._timer = setTimeout(()=>{
        // console.log('   hiding check message')
        var check_message_span = document.getElementById(`id_exports_check_message_${index}`);
        check_message_span.style.display = 'none';
    }, duration);
}